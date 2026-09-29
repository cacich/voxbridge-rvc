def run_cuda_graph(module, key, function, *inputs):
    """Eager inference; avoids GPU graph memory and dynamic shape constraints."""
    return function(*inputs)
