"""zh-TW catalogue -- CLI: help text, update prompt, command output.

Msgids are unique across all area modules (i18n refuses to import otherwise).
"""
from __future__ import annotations

CATALOG: dict[str, str] = {
    # help: per-command
    "help.opt.project": "專案範圍：<dir>/.claude 與 <dir>/.mcp.json（`.` = 目前目錄）",
    "help.opt.dry_run": "只顯示計畫，不做任何變更",
    "help.ui.summary": "互動式選單",
    "help.ui.desc": "瀏覽每個 harness 的項目，用鍵盤暫存變更，一次套用。",
    "help.disable.summary": "停用（暫存）一或多個項目",
    "help.disable.desc": "把項目移出 harness 的視野，不會刪除任何東西。未指定 --harness 時作用於 %s。",
    "help.enable.summary": "還原一或多個項目",
    "help.enable.desc": "把暫存的項目放回去。未指定 --harness 時作用於 %s；"
                        "--all 還原全部（可搭配一個 --harness）。",
    "help.undo.summary": "復原最近一次記錄的批次",
    "help.undo.desc": "依記錄復原最近一次 disable/enable 批次。",
    "help.profile.summary": "儲存 / 套用 / 比較 / 列出具名的啟用項目組合",
    "help.profile.desc": "設定檔記錄哪些項目是啟用的，讓整組設定一步切換。",
    "help.status.summary": "健康檢查",
    "help.status.desc": "顯示狀態檔、每個已安裝的 harness 與其暫存項目，以及值得修正的偏差。",
    "help.list.summary": "目前停用了哪些項目",
    "help.list.desc": "列出所有暫存的項目，可只看一種類型或一個專案。",
    "help.cost.summary": "每個項目的預估啟動 token 數，由大到小",
    "help.cost.desc": "估算每個項目啟動時載入多少 token（字元數/4，±25%），"
                      "以及暫存項目已省下多少。",
    "help.doctor.summary": "檢查 harness 目錄結構與狀態",
    "help.doctor.desc": "比對狀態檔與磁碟；有問題時結束碼為 1。在終端機上，可自動修復的問題"
                        "（chmod、過期項目、孤立檔案、中斷的操作）會逐一詢問 y/n；否則不做任何變更。",
    "help.config.summary": "設定與 Telegram CI 通知",
    "help.config.desc": "不帶動作時開啟設定；`test` 送出一則 Telegram 訊息，"
                        "`sync-ci` 設定 GitHub repo secrets。",
    "help.install_shims.summary": "把 skill shim 寫入每個已安裝的 harness",
    "help.install_shims.desc": "安裝 agent-toggle skill，讓各 harness 的 agent 能呼叫此工具。"
                             "在終端機上，接著逐一詢問 y/n 修復 doctor 能自動修復的問題。",
    "help.migrate.summary": "匯入舊版 ~/.claude-toggle 的狀態",
    "help.migrate.desc": "一次性匯入舊版 claude-toggle 工具的狀態。",
    "help.arg.names": "一或多個項目名稱",
    "help.opt.all": "還原所有停用的項目",
    "help.arg.profile_action": "要執行的動作",
    "help.arg.profile_target": "設定檔名稱或 JSON 檔",
    "help.opt.out": "save：改寫到這個檔案",
    "help.opt.profile_dry_run": "apply：只顯示計畫，不做任何變更",
    "help.opt.type": "只看這種類型：%s",
    "help.opt.list_project": "只看這個專案的項目",
    "help.opt.cost_project": "計算此專案的 .claude 與 .mcp.json，而非使用者範圍",
    "help.arg.config_action": "選用的動作",
    "help.opt.repo": "sync-ci：目標 repo（預設：目前這個）",
    "help.opt.config_dry_run": "sync-ci：只顯示計畫，不做任何設定",
    # help: overview
    "help.group.toggle": "切換",
    "help.group.inspect": "檢視",
    "help.group.setup": "設定",
    "help.tagline": "暫時停用 / 重新啟用各 harness 的 AI agent 資源",
    "help.usage": "用法",
    "help.global_options": "全域選項",
    "help.examples": "範例",
    "help.exit_codes": "結束碼",
    "help.exit_codes_text": "0 成功 · 1 部分失敗 · 2 用法錯誤 · 3 已鎖定 · "
                            "4 不支援的組合 / harness 未安裝",
    "help.footer": "執行 `%s help <command>` 查看詳情 · 每個指令也接受前置 --",
    "help.global_pointer": "全域選項（--harness、--json、--color、-v）：執行 `%s help`",
    "help.opt.harness": "目標 harness（預設：%s）",
    "help.opt.color": "彩色輸出（預設：auto；遵循 NO_COLOR）",
    "help.opt.json": "只輸出一份 JSON 文件，不輸出文字",
    "help.opt.verbose": "發生非預期錯誤時印出 traceback",
    "help.opt.version": "印出版本",
    "help.opt.help": "顯示說明（`help <command>` 看單一指令）",
    "help.arguments": "參數",
    "help.options": "選項",
    # update prompt
    "update.available": "agent-toggle %s 已推出（目前版本 %s）",
    "update.keys": "↑↓ 選擇 · ⏎ 確認 · q/Ctrl+C 略過",
    "update.keys_ascii": "上/下 選擇，Enter 確認，q/Ctrl+C 略過",
    "update.choose": "請選擇 1-3（Enter = 1，q/Ctrl+C = 略過）：",
    "update.now": "立即更新",
    "update.skip": "略過",
    "update.skip_detail": "下次執行再問",
    "update.skip_version": "略過到下一個版本",
    "update.skip_version_detail": "有更新的版本時再問",
    "update.notes": "版本說明：%s",
    "update.failed": "更新未完成 — 請自行執行：%s",
}
