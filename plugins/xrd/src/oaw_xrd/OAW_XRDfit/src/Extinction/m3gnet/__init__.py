"""
The M3GNet framework package
"""
__version__ = "0.2.4"

# Bundled M3GNet weights use TensorFlow checkpoints, which Keras 3 cannot
# restore. This package is loaded only inside the isolated scientific worker.
import os

os.environ["TF_USE_LEGACY_KERAS"] = "1"
