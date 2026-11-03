"""Machine learning: point-in-time features, rate forecasting, fill probability.

Requires the optional ``ml`` extra (numpy, lightgbm). Every model is evaluated
out-of-sample with walk-forward splits and compared with simple baselines; a
learned model is only useful if it beats them.
"""
