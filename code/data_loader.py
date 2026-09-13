import csv, re
from datetime import datetime
from collections import defaultdict

# 16 High-Precision Verified OCR amounts from dataset/media/images/
IMAGE_AMOUNTS = {
    'event_253': 4365000.0,    # image_01 (IDR)
    'event_1442': 100000.0,    # image_02 (INR)
    'event_1545': 41272.0,     # image_03 (INR)
    'event_1700': 2854.0,      # image_04 (INR)
    'event_1786': 704.05,      # image_05 (INR)
    'event_3051': 1995.0,      # image_06 (INR)
    'event_3231': 8528.0,      # image_07 (INR)
    'event_4535': 15339.0,     # image_08 (INR)
    'event_5170': 723.0,       # image_09 (INR)
    'event_6033': 79679.26,    # image_10 (INR)
    'event_6859': 3650.0,      # image_11 (INR)
    'event_7307': 33.50,       # image_12 (USD)
    'event_7941': 2298.0,      # image_13 (INR)
    'event_9421': 4543.0,      # image_14 (INR)
    'event_9806': 9968.0,      # image_15 (INR)
    'event_10521': 393.22,     # image_16 (INR)
}

def parse_date(d_str):
    return datetime.strptime(d_str.strip(), '%Y-%m-%d').date()

def format_date(d):
    return d.strftime('%Y-%m-%d')

class DataLoader:
    def __init__(self, data_dir='dataset'):
        self.data_dir = data_dir
        self.profiles = {}
        self.events_by_user = defaultdict(list)
        self.exchange_rates = {}
        self.messages_by_user = defaultdict(list)
        self.options_by_request = defaultdict(list)
        self.load_all()

    def load_all(self):
        with open(f'{self.data_dir}/exchange_rates.csv', mode='r', encoding='utf-8') as fp:
            for r in csv.DictReader(fp):
                self.exchange_rates[(r['rate_date'], r['from_currency'], r['to_currency'])] = float(r['rate'])

        with open(f'{self.data_dir}/financial_profiles.csv', mode='r', encoding='utf-8') as fp:
            for r in csv.DictReader(fp):
                self.profiles[r['user_id']] = r

        with open(f'{self.data_dir}/messages.csv', mode='r', encoding='utf-8') as fp:
            for r in csv.DictReader(fp):
                self.messages_by_user[r['user_id']].append(r)

        with open(f'{self.data_dir}/request_payment_options.csv', mode='r', encoding='utf-8') as fp:
            for r in csv.DictReader(fp):
                self.options_by_request[r['request_id']].append(r)

        with open(f'{self.data_dir}/financial_events.csv', mode='r', encoding='utf-8') as fp:
            for r in csv.DictReader(fp):
                ev_id = r['event_id']
                amt_str = r['amount'].strip()
                if not amt_str and ev_id in IMAGE_AMOUNTS:
                    amt = IMAGE_AMOUNTS[ev_id]
                elif amt_str:
                    amt = float(amt_str)
                else:
                    amt = 0.0
                r['parsed_amount'] = amt

                user_hc = self.profiles[r['user_id']]['home_currency'] if r['user_id'] in self.profiles else r['currency']
                if r['currency'] != user_hc and amt > 0:
                    rate_key = (r['settlement_date'], r['currency'], user_hc)
                    if rate_key in self.exchange_rates:
                        r['parsed_amount'] = amt * self.exchange_rates[rate_key]
                    else:
                        # Fallback to nearest dated rate for this currency pair
                        matching = [(k[0], v) for k, v in self.exchange_rates.items() if k[1] == r['currency'] and k[2] == user_hc]
                        if matching:
                            ev_d = parse_date(r['settlement_date'])
                            matching.sort(key=lambda x: abs((parse_date(x[0]) - ev_d).days))
                            r['parsed_amount'] = amt * matching[0][1]

                self.events_by_user[r['user_id']].append(r)

    def parse_messages(self, user_id):
        updates = {
            'salary_ended': False,
            'rent_increase_pct': 0.0,
            'salary_amt': None,
            'salary_date': None,      # override for NEXT salary payment date
            'salary_date_shift': None, # employer-notified date shift for recurring payroll
            'extra_inflows': []
        }
        # Sort messages chronologically by sent_at so newer records supersede older ones
        sorted_msgs = sorted(self.messages_by_user[user_id], key=lambda x: x.get('sent_at', ''))
        for m in sorted_msgs:
            txt = m['message_text']
            if any(k in txt.lower() for k in ['employment has ended', 'hubungan kerja anda telah berakhir', 'contract has ended', 'kontrak musiman saat ini telah berakhir']):
                updates['salary_ended'] = True

            # If remaining confirmed salary is mentioned (English or Indonesian), update salary_amt and keep salary active
            m_sisa = re.search(r'(?:sisa gaji|remaining confirmed monthly salary).*?(?:IDR|INR|USD|EUR|ZAR)\s*([\d,]+(?:\.\d+)?)', txt, re.I)
            if m_sisa:
                updates['salary_amt'] = float(m_sisa.group(1).replace(',', ''))
                updates['salary_ended'] = False

            # Check if bank confirms failed debit is still outstanding and will be reattempted
            if any(k in txt.lower() for k in ['outstanding', 'another debit will be attempted', 'masih terbuka']):
                if m.get('related_event_id'):
                    updates.setdefault('retry_failed_events', set()).add(m['related_event_id'])

            m_rent = re.search(r'rent by (\d+)%', txt, re.I)
            if m_rent:
                updates['rent_increase_pct'] = float(m_rent.group(1))

            m_inv = re.search(r'(?:faktur|invoice).*?(?:IDR|INR|USD|EUR|ZAR)\s*([\d,]+(?:\.\d+)?)', txt, re.I)
            if m_inv and 'klien menyetujui' in txt.lower():
                inv_amt = float(m_inv.group(1).replace(',', ''))
                m_date = re.search(r'(\d{4}-\d{2}-\d{2})', txt)
                if m_date:
                    updates['extra_inflows'].append((parse_date(m_date.group(1)), inv_amt))

            # Salary date shift: employer says payroll moved to a different date
            # e.g. "salary is now expected on 2024-09-23", "expected on 2024-09-23"
            m_shift = re.search(
                r'(?:salary|payroll|pay).*?(?:expected|scheduled|confirmed).*?(?:on|by|from)\s*(\d{4}-\d{2}-\d{2})',
                txt, re.I
            )
            if not m_shift:
                m_shift = re.search(
                    r'(?:expected|confirmed).*?on\s*(\d{4}-\d{2}-\d{2})',
                    txt, re.I
                )
            if m_shift and m['source_type'] in ('employer', 'bank'):
                shift_date = m_shift.group(1)
                # Only use if the message doesn't describe an invoice/commission/bonus
                if not any(k in txt.lower() for k in ['invoice', 'commission', 'bonus', 'faktur']):
                    updates['salary_date_shift'] = shift_date

            # Salary amount override from payroll confirmation
            m_sal = re.search(r'(?:gaji|salary|pay).*?(?:IDR|INR|USD|EUR|ZAR)\s*([\d,]+(?:\.\d+)?)', txt, re.I)
            if m_sal and not any(k in txt.lower() for k in ['bonus', 'payout is still pending', 'commission', 'faktur', 'invoice']):
                amt = float(m_sal.group(1).replace(',', ''))
                # Sanity check: salary should be plausible (> 500 to filter token amounts like "EUR 5")
                if amt > 500:
                    m_date = re.search(r'(\d{4}-\d{2}-\d{2})', txt)
                    updates['salary_amt'] = amt
                    if m_date:
                        updates['salary_date'] = m_date.group(1)
        return updates
