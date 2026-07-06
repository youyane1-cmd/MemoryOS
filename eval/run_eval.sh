#!/bin/sh
set -e

python main_loco_parse.py
python evalution_loco.py
