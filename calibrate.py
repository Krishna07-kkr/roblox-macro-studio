"""
🎯 Interactive Visual Coordinate Calibrator & Target Pinpointer
Launches the modern, Apple HIG-inspired visual GUI calibration tool.
"""

import sys
import os

# Set working directory to project root
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from calibrate_gui import launch_calibration_gui


def main():
    launch_calibration_gui()


if __name__ == "__main__":
    main()


