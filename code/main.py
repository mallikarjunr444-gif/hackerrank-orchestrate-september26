import sys, os, csv
from data_loader import DataLoader
from decision_engine import DecisionEngine, validate_row

def main():
    test_mode = '--test' in sys.argv
    base_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(base_dir)
    dataset_dir = os.path.join(repo_root, 'dataset')

    print(f"Loading datasets from {dataset_dir}...")
    loader = DataLoader(data_dir=dataset_dir)
    engine = DecisionEngine(loader)

    if test_mode:
        sample_path = os.path.join(dataset_dir, 'sample_requests.csv')
        print(f"Running evaluation against {sample_path}...")
        with open(sample_path, mode='r', encoding='utf-8') as fp:
            samples = list(csv.DictReader(fp))

        matches = {
            'status': 0,
            'method': 0,
            'earliest': 0,
            'changes': 0,
            'plan': 0,
            'total': len(samples)
        }

        print(f"{'ID':12} | {'Status Calc / GT':35} | {'Method Calc / GT':28} | {'Earliest Calc / GT':25} | {'Changes Calc / GT'}")
        print("-" * 125)
        for req in samples:
            res = engine.evaluate_request(req)
            validate_row(res, req)

            s_m = res['affordability_status'] == req['affordability_status']
            m_m = res['recommended_payment_method'] == req['recommended_payment_method']
            e_m = res['earliest_date_for_full_payment'] == req['earliest_date_for_full_payment']
            c_m = res['spending_changes_needed'] == req['spending_changes_needed']
            p_m = res['payment_plan'] == req['payment_plan']

            if s_m: matches['status'] += 1
            if m_m: matches['method'] += 1
            if e_m: matches['earliest'] += 1
            if c_m: matches['changes'] += 1
            if p_m: matches['plan'] += 1

            print(f"{req['request_id']:12} | {res['affordability_status'][:16]:16} / {req['affordability_status'][:16]:16} | {res['recommended_payment_method'][:12]:12} / {req['recommended_payment_method'][:12]:12} | {res['earliest_date_for_full_payment']:10} / {req['earliest_date_for_full_payment']:10} | {res['spending_changes_needed']:15} / {req['spending_changes_needed']}")

        print("=" * 125)
        tot = matches['total']
        print(f"Benchmark Results on {tot} samples:")
        print(f"  Affordability Status : {matches['status']}/{tot} ({matches['status']/tot*100:.1f}%)")
        print(f"  Payment Method       : {matches['method']}/{tot} ({matches['method']/tot*100:.1f}%)")
        print(f"  Earliest Date        : {matches['earliest']}/{tot} ({matches['earliest']/tot*100:.1f}%)")
        print(f"  Spending Changes     : {matches['changes']}/{tot} ({matches['changes']/tot*100:.1f}%)")
        print(f"  Payment Plan         : {matches['plan']}/{tot} ({matches['plan']/tot*100:.1f}%)")
    else:
        req_path = os.path.join(dataset_dir, 'requests.csv')
        out_path = os.path.join(repo_root, 'output.csv')
        print(f"Generating predictions for {req_path} -> {out_path}...")

        fieldnames = [
            'request_id',
            'amount_safe_to_pay',
            'affordability_status',
            'recommended_payment_method',
            'payment_plan',
            'earliest_date_for_full_payment',
            'spending_changes_needed',
            'decision_explanation'
        ]

        with open(req_path, mode='r', encoding='utf-8') as fp:
            requests = list(csv.DictReader(fp))

        rows_out = []
        for req in requests:
            res = engine.evaluate_request(req)
            validate_row(res, req)
            rows_out.append(res)

        with open(out_path, mode='w', encoding='utf-8', newline='') as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows_out:
                writer.writerow(r)

        print(f"Successfully wrote {len(rows_out)} predictions to {out_path}.")

if __name__ == '__main__':
    main()
