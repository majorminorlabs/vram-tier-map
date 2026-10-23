"""Driver/CUDA compatibility helpers for Vast offer filtering and preflight."""
import re


def version_tuple(value):
    if value is None:
        return None
    match = re.search(r"\d+(?:\.\d+)+", str(value))
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def at_least(actual, required):
    actual_tuple, required_tuple = version_tuple(actual), version_tuple(required)
    if actual_tuple is None or required_tuple is None:
        return False
    size = max(len(actual_tuple), len(required_tuple))
    return actual_tuple + (0,) * (size-len(actual_tuple)) >= required_tuple + (0,) * (size-len(required_tuple))


def offer_supports_cuda(offer, cuda_toolkit, driver_minimum):
    """Use Vast's reported max-good CUDA, falling back to its NVIDIA driver field."""
    cuda_max = version_tuple(offer.get("cuda_max_good"))
    target = version_tuple(cuda_toolkit)
    if cuda_max is not None and target is not None:
        size = max(len(cuda_max), len(target))
        if cuda_max + (0,) * (size-len(cuda_max)) >= target + (0,) * (size-len(target)):
            return True
    return at_least(offer.get("driver_version"), driver_minimum)
