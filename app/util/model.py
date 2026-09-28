def score_product_scale(ph: float, pw: float, ch: float, cw: float) -> float:
    """
    Scores the product scale based on the product height (ph), product width (pw), canvas height (ch), and canvas width (cw).
    Returns a score that indicates how well the product fits within the canvas.
    0 indicates a good fit, where larger values indicate the product is too large and smaller values indicate the product is too small.
    """
    if ph <= 0 or pw <= 0 or ch <= 0 or cw <= 0:
        raise ValueError("Invalid dimensions for scoring product scale")
    eps = 1e-7
    x0 = pw / (eps + ph)
    x1 = cw / (eps + ch)
    x2 = pw / (eps + cw)
    x3 = ph / (eps + ch)
    a1 = max(0, (x0 * -0.1221) + (x1 * 0.1376) + (x2 * 1.0462) + (x3 * 0.2521) + -0.5504)
    a2 = max(0, (x0 * -0.0053) + (x1 * -0.0058) + (x2 * 0.0036) + (x3 * -0.0053) + 0.0001)
    a3 = max(0, (x0 * -0.7698) + (x1 * 0.4682) + (x2 * 0.3553) + (x3 * -0.6023) + 0.2541)
    a4 = max(0, (x0 * -0.0554) + (x1 * 0.5059) + (x2 * -1.5797) + (x3 * -0.4126) + -0.2185)
    a5 = max(0, (x0 * -0.3012) + (x1 * 0.2479) + (x2 * 0.3123) + (x3 * 0.6330) + -0.6455)
    a6 = max(0, (x0 * -0.0156) + (x1 * -0.5566) + (x2 * -0.5470) + (x3 * 0.8597) + -0.1748)
    a7 = max(0, (x0 * 0.0154) + (x1 * -0.0995) + (x2 * -0.8034) + (x3 * -0.8679) + 0.7910)
    a8 = max(0, (x0 * 0.0087) + (x1 * -0.0060) + (x2 * 0.9105) + (x3 * 1.2469) + -0.8703)
    b1 = max(0, (a1 * 0.6181) + (a2 * -0.0037) + (a3 * -0.1136) + (a4 * 0.0578) + (a5 * 0.1385) + (a6 * -2.4023) + (a7 * 0.1141) + (a8 * -1.3241) + 0.6607)
    b2 = max(0, (a1 * 0.4384) + (a2 * -0.0031) + (a3 * 0.2820) + (a4 * -2.2836) + (a5 * -4.0753) + (a6 * 0.3570) + (a7 * -3.0097) + (a8 * -1.0089) + 0.2432)
    b3 = max(0, (a1 * -0.5157) + (a2 * 0.0004) + (a3 * -6.0966) + (a4 * -0.2681) + (a5 * -0.7256) + (a6 * 0.2269) + (a7 * -0.7163) + (a8 * 0.7907) + 0.1173)
    b4 = max(0, (a1 * 0.0012) + (a2 * 0.0042) + (a3 * -0.0065) + (a4 * -0.0040) + (a5 * -0.0052) + (a6 * 0.0001) + (a7 * -0.0055) + (a8 * -0.0054) + -0.0017)
    return (b1 * -1.8143) + (b2 * 2.3816) + (b3 * 1.5851) + (b4 * -0.0017) + 0.2254
