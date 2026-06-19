"""Make the in-tree ``zerith`` package importable when running pytest from the
repo root, without installing anything."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
