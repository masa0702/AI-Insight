#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# .env が無ければ作る
if [ ! -f .env ]; then
  cat > .env <<'EOF'
# 必要なら実キーに置き換え
GOOGLE_API_KEY=
EOF
  echo "'.env' を作成しました。必要なら GOOGLE_API_KEY を設定してください。"
fi

# ---------- 起動 ----------
docker compose up -d --build

# ---------- LAN IP 検出 ----------
detect_ip() {
  # macOS優先: デフォルトゲートウェイ向け経路から出ていくインターフェースIPを取る
  if command -v ipconfig >/dev/null 2>&1; then
    # en0 / en1 の順で試す（Wi-Fiがen1の環境もある）
    for IFACE in en0 en1; do
      IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null || true)
      if [ -n "${IP:-}" ]; then echo "$IP"; return 0; fi
    done
  fi
  # Linux: hostname -I か ip route
  if command -v hostname >/dev/null 2>&1; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -n "${IP:-}" ]; then echo "$IP"; return 0; fi
  fi
  if command -v ip >/dev/null 2>&1; then
    IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for(i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')
    if [ -n "${IP:-}" ]; then echo "$IP"; return 0; fi
  fi
  # 最後の手段
  echo "127.0.0.1"
}

LAN_IP="$(detect_ip)"
PORT="3000"
URL="http://${LAN_IP}:${PORT}"

echo ""
echo "✅ 起動しました。アクセス先:"
echo "   PC:       http://localhost:${PORT}"
echo "   スマホ(LAN): ${URL}"

# 任意: QRコード表示（qrencode がある場合）
if command -v qrencode >/dev/null 2>&1; then
  echo ""
  echo "（QRコードをスマホで読み取ってください）"
  qrencode -t ansiutf8 -o - "${URL}"
fi

echo ""
echo "疎通確認:"
echo "  - フロント:  ${URL}"
echo "  - Health:    ${URL}/healthz  （→ backend へプロキシ）"
echo ""
echo "停止: docker compose down"
