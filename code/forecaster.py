import calendar, math
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from data_loader import parse_date, format_date


def build_forecast(user_id, request_date_str, loader):
    req_d = parse_date(request_date_str)
    end_d = req_d + timedelta(days=90)
    msg_updates = loader.parse_messages(user_id)
    user_events = loader.events_by_user[user_id]

    cash_flows = defaultdict(list)
    # Entry: (amount, category, description, flexibility, event_id, min_allowed)

    # Check if salary ended across messages or any event descriptions
    salary_ended = msg_updates['salary_ended']
    if not salary_ended:
        for e in user_events:
            if e['category'] == 'salary' and 'final' in e['description'].lower():
                salary_ended = True
                break

    # 1. Pending debits (reserve them conservatively)
    for e in user_events:
        if e['status'] == 'pending' and e['direction'] == 'debit':
            s_d = parse_date(e['settlement_date'])
            post_d = max(req_d, s_d)
            if post_d <= end_d:
                cash_flows[post_d].append((-e['parsed_amount'], e['category'], e['description'], 'fixed', e['event_id'], 0.0))

    # 2. Scheduled events
    for e in user_events:
        if e['status'] == 'scheduled':
            s_d = parse_date(e['settlement_date'])
            if req_d <= s_d <= end_d:
                amt = e['parsed_amount']
                if e['direction'] == 'credit':
                    if e['category'] == 'salary':
                        if salary_ended:
                            amt = 0.0
                        elif msg_updates['salary_amt']:
                            amt = msg_updates['salary_amt']
                    cash_flows[s_d].append((amt, e['category'], e['description'], 'fixed', e['event_id'], 0.0))
                else:
                    cash_flows[s_d].append((-amt, e['category'], e['description'], e['flexibility'], e['event_id'], float(e['minimum_allowed_amount'] or 0)))

    # 3. Extra confirmed inflows from messages (e.g. client invoices)
    for inf_date, inf_amt in msg_updates['extra_inflows']:
        if req_d <= inf_date <= end_d:
            cash_flows[inf_date].append((inf_amt, 'invoice', 'Client invoice payment', 'fixed', '', 0.0))

    # 4. Recurring monthly streams
    settled_by_desc = defaultdict(list)
    for e in user_events:
        if e['status'] == 'settled':
            settled_by_desc[(e['category'], e['description'], e['direction'])].append(e)

    var_cats = {'groceries', 'transport'}

    # For salary deduplication: find the primary (most-occurring) salary stream
    # to avoid projecting one-time records (like a retroactive payslip) as recurring
    primary_salary_desc = _get_primary_salary_desc(user_events)

    for (cat, desc, direction), ev_list in settled_by_desc.items():
        if cat in var_cats or cat == 'dining':
            continue
        if len(ev_list) < 2 and cat not in ['rent', 'utilities', 'salary']:
            continue

        # Exclude non-recurring or variable salary items (commissions, bonuses, gig platforms, arrears)
        if cat == 'salary' and any('final' in e['description'].lower() for e in ev_list):
            continue
        if cat == 'salary' and any(k in desc.lower() for k in [
            'delivery platform', 'app earnings', 'marketplace payout', 'driver platform',
            'arrears', 'promotion', 'commission', 'komisi', 'bonus', 'second',
            'performance', 'sales commission', 'monthly sales',
        ]):
            continue

        # Deduplicate salary: only project the primary (most frequent) salary description.
        # This prevents a one-time retroactive payslip (e.g. "August 2019 net salary")
        # from creating a phantom recurring stream alongside the regular "Payroll credit".
        if cat == 'salary' and direction == 'credit':
            if primary_salary_desc and desc != primary_salary_desc and len(ev_list) < 3:
                continue

        ev_list.sort(key=lambda x: parse_date(x['settlement_date']))
        last_d = parse_date(ev_list[-1]['settlement_date'])
        if (req_d - last_d).days > 45 and cat not in ['salary', 'rent', 'utilities']:
            continue

        days = [parse_date(e['settlement_date']).day for e in ev_list]
        day_of_month = Counter(days).most_common(1)[0][0]

        if cat == 'salary':
            if salary_ended:
                amt = 0.0
            elif msg_updates['salary_amt']:
                amt = msg_updates['salary_amt']
            else:
                amt = ev_list[-1]['parsed_amount']
        elif cat == 'rent':
            amt = ev_list[-1]['parsed_amount'] * (1.0 + msg_updates['rent_increase_pct'] / 100.0)
        elif cat == 'utilities':
            amt = sum(e['parsed_amount'] for e in ev_list) / len(ev_list)
        else:
            amt = ev_list[-1]['parsed_amount']

        flex = ev_list[-1]['flexibility']
        min_allowed = float(ev_list[-1]['minimum_allowed_amount'] or 0.0)
        recent_event_id = ev_list[-1]['event_id']

        cur_year, cur_month = req_d.year, req_d.month
        for m_offset in range(0, 4):
            m = cur_month + m_offset
            y = cur_year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            max_d = calendar.monthrange(y, m)[1]
            proj_d = datetime(y, m, min(day_of_month, max_d)).date()

            # If employer signalled a date shift for the upcoming salary, use it
            # (only for the first upcoming occurrence; later ones revert to historical pattern)
            if cat == 'salary' and direction == 'credit' and msg_updates.get('salary_date_shift'):
                shifted = parse_date(msg_updates['salary_date_shift'])
                # Apply the shift only to the month that the shifted date falls in
                if shifted.year == y and shifted.month == m:
                    proj_d = shifted

            if req_d <= proj_d <= end_d:
                already_scheduled = any(c == cat for (a, c, d, f, eid, ma) in cash_flows[proj_d])
                if not already_scheduled and amt > 0:
                    flow_val = amt if direction == 'credit' else -amt
                    cash_flows[proj_d].append((flow_val, cat, desc, flex, recent_event_id, min_allowed))

    # 5. Variable streams with outlier filter (groceries, transport)
    for cat in var_cats:
        ev_list = [e for e in user_events if e['category'] == cat and e['status'] == 'settled']
        if not ev_list:
            continue
        ev_list.sort(key=lambda x: parse_date(x['settlement_date']))
        amts = sorted([e['parsed_amount'] for e in ev_list])
        med_amt = amts[len(amts)//2]
        clean_evs = [e for e in ev_list if e['parsed_amount'] <= 2.5 * med_amt]
        if not clean_evs:
            clean_evs = ev_list

        distinct_dates = sorted(list(set(parse_date(e['settlement_date']) for e in clean_evs)))
        if len(distinct_dates) >= 2:
            diffs = [(distinct_dates[i] - distinct_dates[i-1]).days for i in range(1, len(distinct_dates))]
            interval = round(sum(diffs) / len(diffs))
        else:
            interval = 7

        avg_amt = sum(e['parsed_amount'] for e in clean_evs) / len(clean_evs)
        avg_amt = math.ceil(avg_amt) if avg_amt > 100 else round(avg_amt, 2)
        last_d = distinct_dates[-1]
        next_d = last_d + timedelta(days=interval)
        while next_d <= end_d:
            if next_d >= req_d:
                cash_flows[next_d].append((-avg_amt, cat, f'{cat} spending', 'fixed', clean_evs[-1]['event_id'], 0.0))
            next_d += timedelta(days=interval)

    return cash_flows


def _get_primary_salary_desc(user_events):
    """Return the most frequent settled salary description (excluding commissions/bonuses).
    Used to deduplicate retroactive/one-time payslip records from recurring projection."""
    non_recurring_keywords = [
        'commission', 'komisi', 'bonus', 'arrears', 'promotion', 'performance',
        'sales commission', 'monthly sales', 'delivery platform', 'marketplace payout',
        'app earnings', 'driver platform',
    ]
    salary_descs = [
        e['description'] for e in user_events
        if e['category'] == 'salary' and e['status'] == 'settled' and e['direction'] == 'credit'
        and not any(k in e['description'].lower() for k in non_recurring_keywords)
        and 'final' not in e['description'].lower()
    ]
    if not salary_descs:
        return None
    counts = Counter(salary_descs)
    most_common_desc, most_common_count = counts.most_common(1)[0]
    # Only consider it the "primary" if it occurs more than once
    # (a one-off retroactive record appears exactly once)
    if most_common_count >= 2:
        return most_common_desc
    return None


def simulate(bal0, req_d, cash_flows, payment_schedule, spending_changes=None, num_days=91):
    stopped_eids = set(c[1] for c in (spending_changes or []) if c[0] == 'stop')
    reduced_map = {c[1]: float(c[2]) for c in (spending_changes or []) if c[0] == 'reduce_to'}

    cur_b = bal0
    min_b = cur_b
    daily_balances = {}
    for offset in range(num_days):
        d = req_d + timedelta(days=offset)
        for flow_amt, cat, desc, flex, eid, min_a in cash_flows.get(d, []):
            if flow_amt < 0 and eid in stopped_eids:
                continue
            elif flow_amt < 0 and eid in reduced_map:
                cur_b += -reduced_map[eid]
            else:
                cur_b += flow_amt
        if d in payment_schedule:
            cur_b -= payment_schedule[d]
        daily_balances[offset] = cur_b
        if cur_b < min_b:
            min_b = cur_b
    return min_b, daily_balances
