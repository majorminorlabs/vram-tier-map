#!/usr/bin/env python3
"""Select the cheapest eligible offer from Vast search JSON."""
import json, os, sys, yaml

def verified_status(offer):
    value = offer.get('verified', offer.get('verification', offer.get('vericode', 'unknown')))
    if isinstance(value, bool): return 'verified' if value else 'unverified'
    return str(value)

def filter_offers(raw, cap, requested_gpu, cards, skipped=(), failed=(), smoke=False):
    offers = raw if isinstance(raw, list) else raw.get('offers', [])
    supported = {card['gpu_name']: card for card in cards}
    # Smoke and card runs both draw exclusively from the versioned card registry.
    skipped, failed = set(map(str, skipped)), set(map(str, failed))
    valid = []
    for offer in offers:
        gpu = offer.get('gpu_name')
        card = supported.get(gpu)
        if card is None or (requested_gpu != 'any' and gpu != requested_gpu):
            continue
        disk_gb = int(card['disk_gb'])
        ram_floor_gb = int(card.get('ram_floor_gb', 32))
        if str(offer.get('id', '')) in skipped or str(offer.get('machine_id', '')) in failed:
            continue
        if int(offer.get('num_gpus', 0)) != 1:
            continue
        if not smoke and float(offer.get('cpu_ram', 0)) < ram_floor_gb * 1000:
            continue
        if float(offer.get('reliability', 0)) < .97:
            continue
        if offer.get('rentable') is not True or float(offer.get('dph_total', offer.get('dph', 1e9))) > float(cap):
            continue
        # Smoke host-quality checks are warn-only, so do not use the offer-side
        # RAM/PCIe indicators to disqualify a registered smoke GPU.
        pcie_bw_floor = 8 if int(card['native_pcie_width']) == 16 else 6
        if not smoke and (float(offer.get('pci_gen', 0)) < 3 or float(offer.get('pcie_bw', 0)) < pcie_bw_floor):
            continue
        if float(offer.get('disk_space', 0)) < disk_gb:
            continue
        valid.append({**offer, '_vtm_disk_gb': disk_gb, '_vtm_ram_floor_gb': ram_floor_gb, '_vtm_verified_status': verified_status(offer), '_vtm_native_pcie_gen': card['native_pcie_gen'], '_vtm_native_pcie_width': card['native_pcie_width'], '_vtm_expected_gpu_name':card.get('preflight_gpu_name',gpu)})
    if not valid:
        return None
    cheapest = min(float(x.get('dph_total', x.get('dph', 1e9))) for x in valid)
    near_price_fast = [x for x in valid
                       if float(x.get('dph_total', x.get('dph', 1e9))) <= cheapest + .02
                       and float(x.get('inet_down', 0) or 0) >= 200]
    pool = near_price_fast or valid
    pool.sort(key=lambda x: (float(x.get('dph_total', x.get('dph', 1e9))),
                             -float(x.get('inet_down', 0) or 0),
                             -float(x.get('reliability', 0))))
    return pool[0]

if __name__ == '__main__':
    cap, requested_gpu, registry_path, skip_path, failed_path = sys.argv[1:]
    raw = json.load(sys.stdin)
    cards = yaml.safe_load(open(registry_path))['cards']
    skipped = open(skip_path).read().split() if os.path.exists(skip_path) else []
    failed = open(failed_path).read().split() if os.path.exists(failed_path) else []
    selected = filter_offers(raw, cap, requested_gpu, cards, skipped, failed, len(sys.argv) > 6 and sys.argv[6] == 'smoke')
    if selected is None: raise SystemExit(1)
    print(json.dumps(selected))
