#!/bin/bash
cd ~/zoro-project
NEWV=$(date +%s)
sed -i "s/app_v2.js?v=[0-9]*/app_v2.js?v=${NEWV}/" webapp/index.html
sed -i "s/style.css?v=[0-9]*/style.css?v=${NEWV}/" webapp/index.html
echo "Version bumped to ${NEWV}"
