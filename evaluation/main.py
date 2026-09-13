import sys, os, csv
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_loader import DataLoader
from decision_engine import DecisionEngine, validate_row

def evaluate_samples():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get('DATASET_DIR'),
        os.path.join(base_dir, 'dataset'),
        os.path.join(os.path.dirname(base_dir), 'dataset'),
        os.path.join(os.path.dirname(os.path.dirname(base_dir)), 'dataset'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(base_dir))), 'dataset'),
        os.path.abspath('dataset'),
    ]
    dataset_dir = None
    for cand in candidates:
        if cand and os.path.isdir(cand) and (os.path.exists(os.path.join(cand, 'requests.csv')) or os.path.exists(os.path.join(cand, 'sample_requests.csv'))):
            dataset_dir = os.path.abspath(cand)
            break
    if not dataset_dir:
        dataset_dir = os.path.join(os.path.dirname(os.path.dirname(base_dir)), 'dataset')
    sample_path = os.path.join(dataset_dir, 'sample_requests.csv')
    
    loader = DataLoader(data_dir=dataset_dir)
    engine = DecisionEngine(loader)
    
    with open(sample_path, mode='r', encoding='utf-8') as fp:
        samples = list(csv.DictReader(fp))
        
    matches = {'status': 0, 'method': 0, 'earliest': 0, 'changes': 0, 'plan': 0, 'total': len(samples)}
    
    print("=" * 100)
    print(f"EVALUATING {len(samples)} PUBLIC SAMPLE REQUESTS")
    print("=" * 100)
    for req in samples:
        res = engine.evaluate_request(req)
        validate_row(res, req)
        
        if res['affordability_status'] == req['affordability_status']: matches['status'] += 1
        if res['recommended_payment_method'] == req['recommended_payment_method']: matches['method'] += 1
        if res['earliest_date_for_full_payment'] == req['earliest_date_for_full_payment']: matches['earliest'] += 1
        if res['spending_changes_needed'] == req['spending_changes_needed']: matches['changes'] += 1
        if res['payment_plan'] == req['payment_plan']: matches['plan'] += 1

    tot = matches['total']
    print(f"Accuracy Summary:")
    print(f"  Affordability Status      : {matches['status']}/{tot} ({matches['status']/tot*100:.1f}%)")
    print(f"  Recommended Payment Method: {matches['method']}/{tot} ({matches['method']/tot*100:.1f}%)")
    print(f"  Earliest Date For Full Pay: {matches['earliest']}/{tot} ({matches['earliest']/tot*100:.1f}%)")
    print(f"  Spending Changes Needed   : {matches['changes']}/{tot} ({matches['changes']/tot*100:.1f}%)")
    print(f"  Payment Plan Exact Match  : {matches['plan']}/{tot} ({matches['plan']/tot*100:.1f}%)")
    print("=" * 100)

if __name__ == '__main__':
    evaluate_samples()
