import calendar, math
from datetime import datetime, timedelta
from collections import defaultdict
from data_loader import parse_date, format_date
from forecaster import build_forecast, simulate
import re

def validate_row(row, req):
    safe_amt = float(row['amount_safe_to_pay'])
    req_amt = float(req['requested_amount'])
    assert -1e-4 <= safe_amt <= req_amt + 1e-4, f"Invalid safe amount: {safe_amt} for req {req_amt}"

    assert row['affordability_status'] in ['affordable_now', 'affordable_with_plan', 'affordable_later', 'not_affordable'], \
        f"Invalid status: {row['affordability_status']}"

    assert row['recommended_payment_method'] in ['full_payment', 'partial_payment', 'installments', 'wait', 'not_recommended'], \
        f"Invalid method: {row['recommended_payment_method']}"

    if row['affordability_status'] == 'affordable_now':
        assert row['earliest_date_for_full_payment'] == req['request_date'], \
            f"earliest date {row['earliest_date_for_full_payment']} != req date {req['request_date']}"

    if row['affordability_status'] == 'not_affordable':
        assert row['earliest_date_for_full_payment'] == '', \
            f"earliest date must be empty for not_affordable, got {row['earliest_date_for_full_payment']}"
        assert row['payment_plan'] == 'none', \
            f"payment plan must be none for not_affordable, got {row['payment_plan']}"

    changes = row['spending_changes_needed']
    if changes != 'none':
        parts = changes.split('|')
        assert len(parts) <= 3, f"Too many changes: {changes}"
        seen_events = set()
        for p in parts:
            p_parts = p.split(':')
            assert p_parts[0] in ['stop', 'reduce_to'], f"Invalid change action: {p}"
            eid = p_parts[1]
            assert eid not in seen_events, f"Duplicate event in changes: {eid}"
            seen_events.add(eid)

    plan = row['payment_plan']
    if plan != 'none':
        for item in plan.split('|'):
            assert re.match(r'^\d{4}-\d{2}-\d{2}:[\d\.]+$', item), f"Invalid plan item: {item}"

def fmt_amt(val, curr):
    if curr in ['IDR', 'INR', 'ZAR'] and abs(val - round(val)) < 1e-4:
        return f"{curr} {int(round(val)):,}"
    elif curr in ['USD', 'EUR']:
        return f"{curr} {val:,.2f}"
    else:
        return f"{curr} {val:,.2f}" if abs(val - round(val)) > 1e-4 else f"{curr} {int(round(val)):,}"

def fmt_num(val):
    if abs(val - round(val)) < 1e-4:
        return f"{int(round(val))}"
    else:
        s = f"{val:.2f}"
        return s.rstrip('0').rstrip('.') if '.' in s and s.endswith('00') else s

def format_natural_date(d):
    return f"{d.day} {d.strftime('%B %Y')}"

class DecisionEngine:
    def __init__(self, loader):
        self.loader = loader

    def evaluate_request(self, req):
        req_id = req['request_id']
        u_id = req['user_id']
        req_d = parse_date(req['request_date'])
        due_d = parse_date(req['desired_completion_date'])
        req_amt = float(req['requested_amount'])
        allows_partial = req['allows_partial_payment'].strip().lower() == 'true'

        prof = self.loader.profiles[u_id]
        bal0 = float(prof['current_available_balance'])
        min_keep = float(prof['minimum_balance_to_keep'])
        curr = prof['home_currency']
        considered_methods = set(prof['payment_methods_user_will_consider'].split('|'))
        max_inst_months = int(prof['max_installment_months']) if prof['max_installment_months'].strip() else 0
        prot_cats = set(prof['expense_categories_to_protect'].split('|')) if prof['expense_categories_to_protect'] else set()
        reduce_cats = set(prof['expense_categories_user_is_willing_to_reduce'].split('|')) if prof['expense_categories_user_is_willing_to_reduce'] else set()
        stop_cats = set(prof['expense_categories_user_is_willing_to_stop'].split('|')) if prof['expense_categories_user_is_willing_to_stop'] else set()

        cash_flows = build_forecast(u_id, req['request_date'], self.loader)

        # 1. amount_safe_to_pay today (before optional spending changes)
        min_b_baseline, _ = simulate(bal0, req_d, cash_flows, {})
        raw_safe = min_b_baseline - min_keep
        amount_safe_to_pay = max(0.0, min(req_amt, raw_safe))

        # 2. earliest_date_for_full_payment
        earliest_date = ''
        for offset in range(91):
            eval_d = req_d + timedelta(days=offset)
            min_b_cand, _ = simulate(bal0, req_d, cash_flows, {eval_d: req_amt})
            if min_b_cand >= min_keep - 1e-4:
                earliest_date = format_date(eval_d)
                break

        candidate_plans = []

        # Candidate: full_payment today
        if 'full_payment' in considered_methods:
            min_b_cand, _ = simulate(bal0, req_d, cash_flows, {req_d: req_amt})
            if min_b_cand >= min_keep - 1e-4:
                candidate_plans.append({
                    'status': 'affordable_now',
                    'method': 'full_payment',
                    'plan_str': f"{format_date(req_d)}:{fmt_num(req_amt)}",
                    'earliest_date': format_date(req_d),
                    'changes': [],
                    'total_cost': req_amt,
                    'start_date': req_d,
                    'num_payments': 1,
                    'opt_id': '00',
                    'completes_by_due': req_d <= due_d
                })

        # Candidate: partial_payment
        if allows_partial and 'partial_payment' in considered_methods:
            if 0 < amount_safe_to_pay < req_amt and earliest_date and parse_date(earliest_date) <= due_d:
                p1_d = req_d
                from decimal import Decimal, ROUND_HALF_UP
                d_safe = Decimal(str(amount_safe_to_pay)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                d_req = Decimal(str(req_amt)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                p1_amt = float(d_safe)
                p2_amt = float(d_req - d_safe)
                p2_d = parse_date(earliest_date)
                sched = {p1_d: p1_amt, p2_d: p2_amt}
                min_b_cand, _ = simulate(bal0, req_d, cash_flows, sched)
                if min_b_cand >= min_keep - 1e-4:
                    candidate_plans.append({
                        'status': 'affordable_with_plan',
                        'method': 'partial_payment',
                        'plan_str': f"{format_date(p1_d)}:{fmt_num(p1_amt)}|{format_date(p2_d)}:{fmt_num(p2_amt)}",
                        'earliest_date': earliest_date,
                        'changes': [],
                        'total_cost': req_amt,
                        'start_date': p1_d,
                        'num_payments': 2,
                        'opt_id': '01',
                        'completes_by_due': p2_d <= due_d
                    })

        # Candidate: installments
        if 'installments' in considered_methods:
            for opt in self.loader.options_by_request[req_id]:
                if opt['payment_method'] == 'installments':
                    n_pay = int(opt['number_of_payments'])
                    if max_inst_months > 0 and n_pay > max_inst_months:
                        continue
                    p_amt = float(opt['payment_amount'])
                    first_d = parse_date(opt['first_payment_date'])
                    freq = int(opt['payment_frequency_days']) if opt['payment_frequency_days'] else 30
                    sched = {}
                    plan_items = []
                    last_pay_d = first_d
                    for p_i in range(n_pay):
                        pay_d = first_d + timedelta(days=p_i * freq)
                        sched[pay_d] = p_amt
                        plan_items.append(f"{format_date(pay_d)}:{fmt_num(p_amt)}")
                        last_pay_d = pay_d
                    
                    plan_days = (last_pay_d - req_d).days + 1
                    min_b_cand, _ = simulate(bal0, req_d, cash_flows, sched, num_days=91)
                    if min_b_cand >= min_keep - 1e-4:
                        candidate_plans.append({
                            'status': 'affordable_with_plan',
                            'method': 'installments',
                            'plan_str': '|'.join(plan_items),
                            'earliest_date': earliest_date,
                            'changes': [],
                            'total_cost': float(opt['total_payable_amount']),
                            'start_date': first_d,
                            'num_payments': n_pay,
                            'opt_id': opt['payment_option_id'],
                            'completes_by_due': last_pay_d <= due_d
                        })

        # Candidate: spending changes
        flex_events = {}
        for d, flow_list in cash_flows.items():
            for f_amt, cat, desc, flex, eid, min_a in flow_list:
                if f_amt < 0 and eid and eid not in flex_events:
                    can_stop = cat in stop_cats and flex in ['stoppable', 'reducible_or_stoppable'] and cat not in prot_cats
                    can_reduce = cat in reduce_cats and flex in ['reducible', 'reducible_or_stoppable'] and cat not in prot_cats
                    if can_stop or can_reduce:
                        flex_events[eid] = {
                            'eid': eid, 'cat': cat, 'desc': desc, 'amt': -f_amt,
                            'min_allowed': min_a, 'can_stop': can_stop, 'can_reduce': can_reduce
                        }

        possible_change_sets = []
        ev_items = list(flex_events.values())
        # 1-change sets
        for ev in ev_items:
            if ev['can_stop']:
                possible_change_sets.append([('stop', ev['eid'], ev['desc'])])
            if ev['can_reduce']:
                possible_change_sets.append([('reduce_to', ev['eid'], ev['min_allowed'], ev['desc'])])
        
        # 2-change sets (mutually exclusive events)
        for i in range(len(ev_items)):
            for j in range(i+1, len(ev_items)):
                ev1, ev2 = ev_items[i], ev_items[j]
                c1_list = []
                if ev1['can_stop']: c1_list.append(('stop', ev1['eid'], ev1['desc']))
                if ev1['can_reduce']: c1_list.append(('reduce_to', ev1['eid'], ev1['min_allowed'], ev1['desc']))
                c2_list = []
                if ev2['can_stop']: c2_list.append(('stop', ev2['eid'], ev2['desc']))
                if ev2['can_reduce']: c2_list.append(('reduce_to', ev2['eid'], ev2['min_allowed'], ev2['desc']))
                for c1 in c1_list:
                    for c2 in c2_list:
                        possible_change_sets.append([c1, c2])

        # 3-change sets (mutually exclusive events, max 3)
        for i in range(len(ev_items)):
            for j in range(i+1, len(ev_items)):
                for k in range(j+1, len(ev_items)):
                    ev1, ev2, ev3 = ev_items[i], ev_items[j], ev_items[k]
                    c1_list = [('stop', ev1['eid'], ev1['desc'])] if ev1['can_stop'] else []
                    if ev1['can_reduce']: c1_list.append(('reduce_to', ev1['eid'], ev1['min_allowed'], ev1['desc']))
                    c2_list = [('stop', ev2['eid'], ev2['desc'])] if ev2['can_stop'] else []
                    if ev2['can_reduce']: c2_list.append(('reduce_to', ev2['eid'], ev2['min_allowed'], ev2['desc']))
                    c3_list = [('stop', ev3['eid'], ev3['desc'])] if ev3['can_stop'] else []
                    if ev3['can_reduce']: c3_list.append(('reduce_to', ev3['eid'], ev3['min_allowed'], ev3['desc']))
                    for c1 in c1_list:
                        for c2 in c2_list:
                            for c3 in c3_list:
                                possible_change_sets.append([c1, c2, c3])

        for ch_set in possible_change_sets:
            ch_tuples = [(c[0], c[1], c[2] if c[0] == 'reduce_to' else None) for c in ch_set]
            if 'full_payment' in considered_methods:
                min_b_cand, _ = simulate(bal0, req_d, cash_flows, {req_d: req_amt}, ch_tuples)
                if min_b_cand >= min_keep - 1e-4:
                    candidate_plans.append({
                        'status': 'affordable_with_plan',
                        'method': 'full_payment',
                        'plan_str': f"{format_date(req_d)}:{fmt_num(req_amt)}",
                        'earliest_date': earliest_date,
                        'changes': ch_set,
                        'total_cost': req_amt,
                        'start_date': req_d,
                        'num_payments': 1,
                        'opt_id': '00',
                        'completes_by_due': req_d <= due_d
                    })

        # Candidate: wait
        if 'full_payment' in considered_methods and earliest_date and earliest_date != format_date(req_d):
            e_d = parse_date(earliest_date)
            candidate_plans.append({
                'status': 'affordable_later',
                'method': 'wait',
                'plan_str': f"{earliest_date}:{fmt_num(req_amt)}",
                'earliest_date': earliest_date,
                'changes': [],
                'total_cost': req_amt,
                'start_date': e_d,
                'num_payments': 1,
                'opt_id': '99',
                'completes_by_due': e_d <= due_d
            })

        # Rank plans according to Problem Statement §Choosing Between Safe Plans
        # 1. Complete full request by desired_completion_date
        # 2. Require no spending changes
        # 3. Minimize total amount paid
        # 4. Start payment earlier
        # 5. Use fewer payments
        # 6. Lowest payment_option_id
        def plan_rank(p):
            comp_on_time = 0 if p['completes_by_due'] else 1
            num_changes = len(p['changes'])
            cost = p['total_cost']
            start_d = p['start_date']
            n_pay = p['num_payments']
            opt_id = p['opt_id']
            return (comp_on_time, num_changes, cost, start_d, n_pay, opt_id)

        on_time_candidates = [p for p in candidate_plans if p['completes_by_due']]
        if on_time_candidates:
            on_time_candidates.sort(key=plan_rank)
            chosen = on_time_candidates[0]
        else:
            chosen = {
                'status': 'not_affordable',
                'method': 'not_recommended',
                'plan_str': 'none',
                'earliest_date': '',
                'changes': [],
                'total_cost': 0,
                'start_date': req_d,
                'num_payments': 0,
                'opt_id': 'none',
                'completes_by_due': False
            }

        # Build changes string
        if not chosen['changes']:
            changes_str = 'none'
        else:
            c_strs = []
            for c in chosen['changes']:
                if c[0] == 'stop':
                    c_strs.append(f"stop:{c[1]}")
                elif c[0] == 'reduce_to':
                    c_strs.append(f"reduce_to:{c[1]}:{fmt_num(c[2])}")
            changes_str = '|'.join(c_strs)

        # Build grounded explanation
        expl = self.build_explanation(chosen, req, prof, amount_safe_to_pay)

        return {
            'request_id': req_id,
            'amount_safe_to_pay': fmt_num(amount_safe_to_pay),
            'affordability_status': chosen['status'],
            'recommended_payment_method': chosen['method'],
            'payment_plan': chosen['plan_str'],
            'earliest_date_for_full_payment': chosen['earliest_date'],
            'spending_changes_needed': changes_str,
            'decision_explanation': expl
        }

    def build_explanation(self, plan, req, prof, safe_amt):
        curr = prof['home_currency']
        min_keep = float(prof['minimum_balance_to_keep'])
        req_amt = float(req['requested_amount'])
        req_d = parse_date(req['request_date'])
        due_d = parse_date(req['desired_completion_date'])
        method = plan['method']
        status = plan['status']

        if status == 'affordable_now' and method == 'full_payment':
            return f"Pay {fmt_amt(req_amt, curr)} today. This leaves at least {fmt_amt(min_keep, curr)} available over the next 90 days."

        elif status == 'affordable_with_plan':
            if method == 'installments':
                n_pay = plan['num_payments']
                first_entry = plan['plan_str'].split('|')[0]
                p_date_str, p_amt_str = first_entry.split(':')
                p_date = parse_date(p_date_str)
                p_amt = float(p_amt_str)
                return f"Use {n_pay} installments of {fmt_amt(p_amt, curr)}, starting {format_natural_date(p_date)}. This leaves at least {fmt_amt(min_keep, curr)} available."
            elif method == 'partial_payment':
                p1_str, p2_str = plan['plan_str'].split('|')
                _, a1_str = p1_str.split(':')
                d2_str, a2_str = p2_str.split(':')
                d2 = parse_date(d2_str)
                return f"Pay {fmt_amt(float(a1_str), curr)} today and the remaining {fmt_amt(float(a2_str), curr)} on {format_natural_date(d2)}. This completes the full request and keeps the {fmt_amt(min_keep, curr)} minimum protected."
            elif method == 'full_payment':
                change_descs = []
                for c in plan['changes']:
                    if c[0] == 'stop':
                        desc_clean = c[2].lower()
                        change_descs.append(f"stop the {desc_clean}")
                    elif c[0] == 'reduce_to':
                        desc_clean = c[3].lower() if len(c) > 3 else c[2]
                        new_amt = float(c[2]) if len(c) <= 3 else float(c[2])
                        change_descs.append(f"reduce the {desc_clean} to {fmt_amt(new_amt, curr)}")
                changes_text = ' and '.join(change_descs).capitalize()
                return f"{changes_text}, then pay {fmt_amt(req_amt, curr)} today. This leaves at least {fmt_amt(min_keep, curr)} available."

        elif status == 'affordable_later' and method == 'wait':
            e_d = parse_date(plan['earliest_date'])
            return f"Pay {fmt_amt(req_amt, curr)} in full on {format_natural_date(e_d)}. Paying earlier would take the balance below the {fmt_amt(min_keep, curr)} minimum."

        else: # not_affordable
            if safe_amt > 0 and not plan['completes_by_due']:
                return f"Do not proceed with the {fmt_amt(req_amt, curr)} request. Although {fmt_amt(safe_amt, curr)} is available today, the full amount cannot be completed safely within 90 days."
            else:
                return f"Do not make this payment by {format_natural_date(due_d)}. None of the available options keeps the {fmt_amt(min_keep, curr)} minimum protected."
