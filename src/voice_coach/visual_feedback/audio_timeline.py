"""Classify bounded timestamp quantization; never accept drift or real gaps."""
from fractions import Fraction

MAX_JITTER_SECONDS = Fraction(2, 1000)


def sample_clock(stream, frames):
    """Use exact rational arithmetic and bound local AND cumulative error.

    A positive result permits rewriting a *new derivative's* frame PTS from
    sample counts, anchored to the original first decoded sample. It does not
    assert that old, irregular timestamps were sample-exact.
    """
    try:
        rate, base = int(stream['sample_rate']), Fraction(stream['time_base'])
        rows = [r for r in frames if r.get('stream_index') == stream['index']]
        if not rows or not 8000 <= rate <= 192000 or base <= 0:
            return None
        start = int(rows[0]['best_effort_timestamp']) * base
        samples, previous_end, previous_pts, maximum = 0, start, None, Fraction(0)
        for row in rows:
            pts, count = int(row['best_effort_timestamp']) * base, int(row['nb_samples'])
            error = max(abs(pts - previous_end), abs(pts - start - Fraction(samples, rate)))
            if (count <= 0 or error > MAX_JITTER_SECONDS
                    or previous_pts is not None and pts <= previous_pts):
                return None
            maximum = max(maximum, error)
            previous_end, previous_pts = pts + Fraction(count, rate), pts
            samples += count
        if not 0 < Fraction(samples, rate) <= 120 or abs(start) > 86400:
            return None
        return dict(firstPtsNumerator=start.numerator, firstPtsDenominator=start.denominator,
                    sampleRate=rate, samples=samples, maximumJitterSeconds=float(maximum),
                    requiresRegularization=maximum > Fraction(1, rate))
    except (KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
