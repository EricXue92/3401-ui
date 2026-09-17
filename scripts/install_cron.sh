#!/bin/bash
# 事件管理系统 cron 新增（只增不改，遵守铁律）
# 工作空间: /home/sdadmin/.openclaw/workspace/projects/event-management-system
cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts || exit 1

# 现有 crontab
( crontab -l 2>/dev/null ) > /tmp/ev_cron_current.txt

# 追加事件管理系统的条目（先检查是否已存在，避免重复）
add_if_missing() {
    local pattern="$1"; local line="$2"
    if grep -qF "$pattern" /tmp/ev_cron_current.txt; then
        echo "SKIP (已存在): $pattern"
    else
        echo "$line" >> /tmp/ev_cron_current.txt
        echo "ADD: $pattern"
    fi
}

# 1. 经济日历每日采集 (每天 18:00 HKT 采当天 + 次日)
add_if_missing "event_eco_daily" \
"0 18 * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_events.py --economic \$(date +\%Y\%m\%d) >> /tmp/event_map/economic.log 2>&1"

# 2. A股财报披露每日采集 (每天 18:05)
add_if_missing "event_cn_earn_daily" \
"5 18 * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_events.py --cn-earnings >> /tmp/event_map/cn_earnings.log 2>&1"

# 3. 复检每30分钟
add_if_missing "event_recheck" \
"*/30 * * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 recheck_events.py >> /tmp/event_map/recheck.log 2>&1"

# 4. 美股财报补采 (每3天 06:00 全量刷新, 保持未来财报日历新鲜)
add_if_missing "event_us_earn_refresh" \
"0 6 */3 * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_us_earnings_future.py >> /tmp/event_map/us_earnings.log 2>&1"

# 5. 全球已排程事件 (每30分钟)
add_if_missing "collect_global_events.py --calendar" \
"*/30 * * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_global_events.py --calendar >> /tmp/event_map/global_events.log 2>&1"

# 6. 全球突发快讯 (每3分钟)
add_if_missing "collect_global_events.py --breaking" \
"*/3 * * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_global_events.py --breaking >> /tmp/event_map/global_events.log 2>&1"

# 写回
crontab /tmp/ev_cron_current.txt
echo "=== 已生效 ==="
crontab -l | grep -E "event_map|event_eco|event_cn|event_recheck|event_us" | head
