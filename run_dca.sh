# file: run_dca.sh
#!/usr/bin/env bash
set -euo pipefail

# Переходим в директорию проекта (относительно расположения скрипта)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

# Логи
mkdir -p ./logs
echo "[$(date -Iseconds)] Starting DCA run" >> ./logs/dca.log

# Активация venv, если есть
if [[ -d ./.venv ]]; then
	# shellcheck disable=SC1091
	source ./.venv/bin/activate
fi

# Конфигурация через переменные окружения (можно переопределять из cron/systemd)
RUB_BUDGET="${RUB_BUDGET:-5000}"
CONFIG_FILE="${CONFIG_FILE:-config.yml}"
DAY_OF_MONTH="${DAY_OF_MONTH:-5}"   # 1..28, по умолчанию 5
# Если ALLOW_WEEKEND непустая (например, "1"), добавим флаг --allow-weekend
ALLOW_WEEKEND="${ALLOW_WEEKEND:-}"

ARGS=(
	--config "$CONFIG_FILE"
	--rub-budget "$RUB_BUDGET"
	--day-of-month "$DAY_OF_MONTH"
)

if [[ -n "$ALLOW_WEEKEND" ]]; then
	ARGS+=(--allow-weekend)
fi

# Запуск
python3 main.py "${ARGS[@]}" >> ./logs/dca.log 2>&1