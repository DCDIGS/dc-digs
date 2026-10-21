#!/bin/bash
# Double-click to stop DC Digs and remove it from your login items.
cd "$(dirname "$0")" && bash install/mac/uninstall.sh
echo; read -r -p "Press Return to close this window. " _
