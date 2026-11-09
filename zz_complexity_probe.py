"""Draft lines to exercise the complexity report. Do not merge."""


def draft_probe(items, limit):
    total = 0
    for item in items:
        if item:
            for step in range(limit):
                if step % 2 and item > step:
                    total += step
                elif step > limit / 2:
                    total -= 1
    return total
