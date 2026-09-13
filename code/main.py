import sys, os, csv
from data_loader import DataLoader
from decision_engine import DecisionEngine, validate_row

def evaluate_predictions(preds_map, ground_truth_rows):
    """
    Evaluates predictions against ground-truth rows and prints detailed accuracy.
    Matches the required benchmark scoring rubric.
    """
    matches = {
        'safe': 0,
        'status': 0,
        'method': 0,
        'earliest': 0,
        'changes': 0,
        'plan': 0,
        'exact_all': 0,
        'total': len(ground_truth_rows)
    }

    print(f"{'ID':12} | {'Status Calc / GT':35} | {'Method Calc / GT':28} | {'Earliest Calc / GT':25} | {'Changes Calc / GT'}")
    print("-" * 125)
    for gt in ground_truth_rows:
        rid = gt['request_id']
        pred = preds_map.get(rid)
        if not pred:
            continue

        try:
            safe_m = abs(float(pred['amount_safe_to_pay']) - float(gt['amount_safe_to_pay'])) <= 1.0
        except Exception:
            safe_m = False

        s_m = pred['affordability_status'] == gt['affordability_status']
        m_m = pred['recommended_payment_method'] == gt['recommended_payment_method']
        e_m = pred['earliest_date_for_full_payment'] == gt['earliest_date_for_full_payment']
        c_m = pred['spending_changes_needed'] == gt['spending_changes_needed']
        p_m = pred['payment_plan'] == gt['payment_plan']
        all_m = safe_m and s_m and m_m and e_m and c_m and p_m

        if safe_m: matches['safe'] += 1
        if s_m: matches['status'] += 1
        if m_m: matches['method'] += 1
        if e_m: matches['earliest'] += 1
        if c_m: matches['changes'] += 1
        if p_m: matches['plan'] += 1
        if all_m: matches['exact_all'] += 1

        print(f"{rid:12} | {pred['affordability_status'][:16]:16} / {gt['affordability_status'][:16]:16} | {pred['recommended_payment_method'][:12]:12} / {gt['recommended_payment_method'][:12]:12} | {pred['earliest_date_for_full_payment']:10} / {gt['earliest_date_for_full_payment']:10} | {pred['spending_changes_needed']:15} / {gt['spending_changes_needed']}")

    print("=" * 125)
    tot = matches['total']
    print(f"Benchmark Results on {tot} samples:")
    print(f"  amount_safe_to_pay   : {matches['safe']}/{tot} ({matches['safe']/tot*100:.1f}%)")
    print(f"  affordability_status : {matches['status']}/{tot} ({matches['status']/tot*100:.1f}%)")
    print(f"  payment_method       : {matches['method']}/{tot} ({matches['method']/tot*100:.1f}%)")
    print(f"  earliest_date        : {matches['earliest']}/{tot} ({matches['earliest']/tot*100:.1f}%)")
    print(f"  spending_changes     : {matches['changes']}/{tot} ({matches['changes']/tot*100:.1f}%)")
    print(f"  payment_plan         : {matches['plan']}/{tot} ({matches['plan']/tot*100:.1f}%)")
    print(f"  Exact match (all)    : {matches['exact_all']}/{tot} ({matches['exact_all']/tot*100:.1f}%)")
    return matches


def main():
    test_mode = ('--test' in sys.argv) or ('--evaluate-samples-only' in sys.argv) or ('--eval-samples' in sys.argv)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get('DATASET_DIR'),
        os.path.join(base_dir, 'dataset'),
        os.path.join(os.path.dirname(base_dir), 'dataset'),
        os.path.join(os.path.dirname(os.path.dirname(base_dir)), 'dataset'),
        os.path.abspath('dataset'),
    ]
    dataset_dir = None
    for cand in candidates:
        if cand and os.path.isdir(cand) and (os.path.exists(os.path.join(cand, 'requests.csv')) or os.path.exists(os.path.join(cand, 'sample_requests.csv'))):
            dataset_dir = os.path.abspath(cand)
            break
    if not dataset_dir:
        dataset_dir = os.path.join(os.path.dirname(base_dir), 'dataset')

    repo_root = os.path.dirname(dataset_dir)
    requests_path = os.path.join(dataset_dir, 'requests.csv')
    if '--requests-file' in sys.argv:
        idx = sys.argv.index('--requests-file')
        custom_req = sys.argv[idx + 1]
        if os.path.isabs(custom_req):
            requests_path = custom_req
        else:
            requests_path = os.path.join(repo_root, custom_req) if not os.path.exists(custom_req) else custom_req
        if 'sample_requests' in requests_path:
            test_mode = True

    # --sample-out <path>: write sample predictions to a CSV for external evaluation
    sample_out_path = None
    if '--sample-out' in sys.argv:
        idx = sys.argv.index('--sample-out')
        sample_out_path = sys.argv[idx + 1]
        test_mode = True

    print(f"Loading datasets from {dataset_dir}...")
    loader = DataLoader(data_dir=dataset_dir)
    engine = DecisionEngine(loader)

    if test_mode:
        sample_path = requests_path if 'sample_requests' in requests_path else os.path.join(dataset_dir, 'sample_requests.csv')
        print(f"Running evaluation against {sample_path}...")
        with open(sample_path, mode='r', encoding='utf-8') as fp:
            samples = list(csv.DictReader(fp))

        preds_map = {}
        for req in samples:
            res = engine.evaluate_request(req)
            validate_row(res, req)
            preds_map[req['request_id']] = res

        matches = evaluate_predictions(preds_map, samples)

        # Write sample predictions to file if requested
        if sample_out_path:
            fieldnames = [
                'request_id', 'amount_safe_to_pay', 'affordability_status',
                'recommended_payment_method', 'payment_plan',
                'earliest_date_for_full_payment', 'spending_changes_needed',
                'decision_explanation'
            ]
            with open(sample_out_path, mode='w', encoding='utf-8', newline='') as fp:
                writer = csv.DictWriter(fp, fieldnames=fieldnames)
                writer.writeheader()
                for r in preds_map.values():
                    writer.writerow(r)
            print(f"Sample predictions written to {sample_out_path}")
    else:
        req_path = requests_path
        out_path = os.path.join(repo_root, 'output.csv')
        if '--output' in sys.argv:
            idx = sys.argv.index('--output')
            out_path = sys.argv[idx + 1]

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

        # Also write to local directory output.csv if out_path was in repo_root
        local_out = 'output.csv'
        if os.path.abspath(out_path) != os.path.abspath(local_out):
            try:
                with open(local_out, mode='w', encoding='utf-8', newline='') as fp:
                    writer = csv.DictWriter(fp, fieldnames=fieldnames)
                    writer.writeheader()
                    for r in rows_out:
                        writer.writerow(r)
            except Exception:
                pass

        print(f"Successfully wrote {len(rows_out)} predictions to {out_path}.")

if __name__ == '__main__':
    main()

