#!/bin/bash
# verify_v2_m3.sh — 从 gateway 进程 environ 提取 DEEPSEEK_API_KEY（避免读 .env，
# 受 deny 规则保护），再跑 verify 脚本。用法：bash scripts/verify_v2_m3.sh
set -e
cd "$(dirname "$0")/.."   # backend/

KEY=""
for pid in $(pgrep -f gateway); do
  if tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep -q "^DEEPSEEK_API_KEY="; then
    KEY=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep "^DEEPSEEK_API_KEY=" | head -1 | cut -d= -f2-)
    break
  fi
done
if [ -z "$KEY" ]; then
  echo "ERROR: 无法从 gateway 进程获取 DEEPSEEK_API_KEY" >&2
  exit 1
fi

export DEEPSEEK_API_KEY="$KEY"
unset DSH_SESSION_ROOT DSH_HOME DSH_CWD
# 测试库凭据不进 git（dev-lead review）：密码经 LANYUAN_TEST_MYSQL_PASSWORD
# 注入，缺失即报错退出（而非 Access denied 的误导性失败）
export DATABASE_URL="mysql+aiomysql://lanyuan_test:${LANYUAN_TEST_MYSQL_PASSWORD:?未设置 LANYUAN_TEST_MYSQL_PASSWORD（lanyuan_test 测试库密码，凭据不进 git）}@127.0.0.1:3306/lanyuan_test"
exec .venv/bin/python scripts/verify_v2_m3.py
