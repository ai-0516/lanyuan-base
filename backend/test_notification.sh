#!/usr/bin/env bash
# 通知功能手动测试脚本
# 用法: bash test_notification.sh
# 假设后端运行在 http://localhost:8000

BASE="http://localhost:8000/api/v1"

echo "===== 1. 用户 A 登录 ====="
A=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"code":"user_a","nickname":"用户A"}')
TOKEN_A=$(echo "$A" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
A_ID=$(echo "$A" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['user']['id'])")
echo "用户A ID=$A_ID  token=$TOKEN_A"

echo ""
echo "===== 2. 用户 B 登录 ====="
B=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"code":"user_b","nickname":"用户B"}')
TOKEN_B=$(echo "$B" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
B_ID=$(echo "$B" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['user']['id'])")
echo "用户B ID=$B_ID  token=$TOKEN_B"

echo ""
echo "===== 3. 用户 A 发帖子 ====="
POST=$(curl -s -X POST "$BASE/posts" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN_A" \
  -d '{"content":"通知测试帖子","images":[]}')
POST_ID=$(echo "$POST" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
echo "帖子ID=$POST_ID"

echo ""
echo "===== 4. 用户 B 点赞帖子（产生 like 通知）====="
curl -s -X POST "$BASE/posts/$POST_ID/like" \
  -H "Authorization: Bearer $TOKEN_B" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r)"

echo ""
echo "===== 5. 用户 B 评论帖子（产生 comment 通知）====="
curl -s -X POST "$BASE/posts/$POST_ID/comments" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN_B" \
  -d '{"content":"用户B的评论"}' | python3 -c "import sys,json; r=json.load(sys.stdin); print(r)"

echo ""
echo "===== 6. 用户 A 查看通知 ====="
curl -s "$BASE/notifications" \
  -H "Authorization: Bearer $TOKEN_A" | python3 -c "
import sys,json
r = json.load(sys.stdin)
for n in r['data']:
    print(f\"  [{n['type']}] 来自用户 {n['from_user']['nickname']} (帖子{n['post_id']}) 已读={n['is_read']}\")
"

echo ""
echo "===== 7. 用户 A 查看未读数 ====="
curl -s "$BASE/notifications/count" \
  -H "Authorization: Bearer $TOKEN_A" | python3 -c "import sys,json; r=json.load(sys.stdin); print(f\"未读: {r['data']['count']}\")"

echo ""
echo "===== 8. 用户 A 标记为已读 ====="
curl -s -X PUT "$BASE/notifications/read-all" \
  -H "Authorization: Bearer $TOKEN_A" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r)"

echo ""
echo "===== 9. 再次查看未读数 ====="
curl -s "$BASE/notifications/count" \
  -H "Authorization: Bearer $TOKEN_A" | python3 -c "import sys,json; r=json.load(sys.stdin); print(f\"未读: {r['data']['count']}\")"

echo ""
echo "===== 完成！====="
