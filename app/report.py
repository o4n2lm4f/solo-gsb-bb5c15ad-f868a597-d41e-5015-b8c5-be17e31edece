"""样本审计报告导出：Markdown / HTML / JSON。"""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from .audit import OPEN_LABEL

STATUS_TEXT = {
    "pass": "审计通过",
    "warning": "审计通过（存在告警）",
    "fail": "审计未通过",
    "empty": "无数据",
}

FINDING_TEXT = {
    "gap": "保管断档",
    "overlap": "交接重叠",
    "reversed_interval": "起止倒置",
    "out_of_order": "逆序录入",
    "bad_time": "时间格式错误",
}

SIGNOFF_STATUS_TEXT = {
    "active": "已签署",
    "revoked": "已撤销",
}


def _md_cell(text: Any) -> str:
    """转义 Markdown 表格单元格中的竖线。"""
    return str(text if text not in (None, "") else "—").replace("|", "\\|")


def build_markdown(
    sample: dict[str, Any],
    audit: dict[str, Any],
    signoffs: list[dict[str, Any]] | None = None,
) -> str:
    s = audit["summary"]
    lines = [
        f"# 样本交接链路审计报告",
        "",
        f"- **样本编号**：{sample['code']}",
        f"- **样本名称**：{sample['name']}",
        f"- **审计结论**：{STATUS_TEXT.get(audit['status'], audit['status'])}",
        f"- **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 结论",
        "",
        audit["conclusion"],
        "",
        "## 统计",
        "",
        f"| 交接记录 | 有效记录 | 断档 | 重叠 | 起止倒置 | 逆序录入 | 未归还 |",
        f"| --- | --- | --- | --- | --- | --- | --- |",
        f"| {s['total']} | {s['valid']} | {s['gaps']} | {s['overlaps']} | "
        f"{s['reversed']} | {s['out_of_order']} | {s['open_holds']} |",
        "",
    ]
    if audit["findings"]:
        lines += ["## 审计发现", ""]
        for i, f in enumerate(audit["findings"], 1):
            lines.append(f"{i}. **[{FINDING_TEXT.get(f['code'], f['code'])}]** {f['message']}")
        lines.append("")

    lines += ["## 交接事件（按起始时间排序）", ""]
    if audit["events"]:
        lines += [
            "| # | 录入序 | 经手人 | 起始时间 | 结束时间 | 保管时长 | 备注 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for i, e in enumerate(audit["events"], 1):
            end = OPEN_LABEL if e["open"] else e["end_time"]
            mark = " ⚠️" if e["has_conflict"] else ""
            lines.append(
                f"| {i}{mark} | {e['seq']} | {e['custodian']} | {e['start_time']} | "
                f"{end} | {e['duration']} | {e['note'] or '—'} |"
            )
        lines.append("")
    else:
        lines += ["_暂无交接事件。_", ""]

    lines += _signoff_markdown(signoffs)
    return "\n".join(lines)


def _signoff_markdown(signoffs: list[dict[str, Any]] | None) -> list[str]:
    lines = ["## 结论签署", ""]
    if not signoffs:
        return lines + ["_尚未有任何签署记录。_", ""]
    lines += [
        "| 签署人 | 签署时间 | 状态 | 签署时审计结论 | 快照校验值 | 备注 | 撤销信息 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in signoffs:
        status = SIGNOFF_STATUS_TEXT.get(s["status"], s["status"])
        audit_txt = STATUS_TEXT.get(s.get("audit_status") or "", s.get("audit_status") or "—")
        revoke = "—"
        if s["status"] == "revoked":
            revoke = f"{s.get('revoked_at') or ''} {s.get('revoke_reason') or ''}".strip()
        lines.append(
            f"| {_md_cell(s['signer'])} | {_md_cell(s['signed_at'])} | {status} | "
            f"{audit_txt} | {_md_cell(s.get('digest'))} | {_md_cell(s.get('remark'))} | "
            f"{_md_cell(revoke)} |"
        )
    lines.append("")
    return lines


def build_html(
    sample: dict[str, Any],
    audit: dict[str, Any],
    signoffs: list[dict[str, Any]] | None = None,
) -> str:
    s = audit["summary"]
    status_class = {"pass": "ok", "warning": "warn", "fail": "bad", "empty": "muted"}.get(
        audit["status"], "muted"
    )
    finding_rows = ""
    for i, f in enumerate(audit["findings"], 1):
        finding_rows += (
            f"<li><span class='tag tag-{f['code']}'>"
            f"{FINDING_TEXT.get(f['code'], f['code'])}</span>{html.escape(f['message'])}</li>"
        )
    if not finding_rows:
        finding_rows = "<li class='muted'>未发现异常。</li>"

    event_rows = ""
    for i, e in enumerate(audit["events"], 1):
        end = (
            f"<span class='open'>{html.escape(e['end_time'])} 未归还</span>"
            if e["open"]
            else html.escape(e["end_time"])
        )
        row_cls = " class='row-conflict'" if e["has_conflict"] else ""
        event_rows += (
            f"<tr{row_cls}><td>{i}</td><td>{e['seq']}</td>"
            f"<td>{html.escape(e['custodian'])}</td>"
            f"<td>{html.escape(e['start_time'])}</td><td>{end}</td>"
            f"<td>{html.escape(e['duration'])}</td>"
            f"<td>{html.escape(e['note'] or '—')}</td></tr>"
        )

    signoff_rows = ""
    for so in signoffs or []:
        st_cls = "ok" if so["status"] == "active" else "bad"
        st_txt = SIGNOFF_STATUS_TEXT.get(so["status"], so["status"])
        audit_txt = STATUS_TEXT.get(so.get("audit_status") or "", so.get("audit_status") or "—")
        revoke = "—"
        if so["status"] == "revoked":
            revoke = html.escape(
                f"{so.get('revoked_at') or ''} {so.get('revoke_reason') or ''}".strip()
            )
        signoff_rows += (
            f"<tr><td>{html.escape(so['signer'])}</td>"
            f"<td>{html.escape(so['signed_at'])}</td>"
            f"<td><span class='status {st_cls}'>{st_txt}</span></td>"
            f"<td>{html.escape(audit_txt)}</td>"
            f"<td><code>{html.escape(so.get('digest') or '')}</code></td>"
            f"<td>{html.escape(so.get('remark') or '—')}</td>"
            f"<td>{revoke}</td></tr>"
        )
    if not signoff_rows:
        signoff_rows = "<tr><td colspan='7' class='muted'>尚未有任何签署记录。</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>审计报告 - {html.escape(sample['code'])}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 40px; color: #1f2933; background: #fafbfc; }}
  .card {{ background: #fff; border: 1px solid #e4e7eb; border-radius: 10px;
           padding: 24px 28px; margin-bottom: 20px; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; }}
  .meta {{ color: #616e7c; font-size: 13px; margin-top: 8px; }}
  .status {{ display: inline-block; padding: 4px 12px; border-radius: 999px;
             font-weight: 600; font-size: 13px; }}
  .ok {{ background: #e6f7ed; color: #1a7f4b; }}
  .warn {{ background: #fff6e0; color: #9a6b00; }}
  .bad {{ background: #fdecec; color: #c0392b; }}
  .muted {{ background: #eef1f4; color: #616e7c; }}
  .conclusion {{ font-size: 15px; line-height: 1.7; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #e4e7eb; padding: 8px 10px; text-align: left; }}
  th {{ background: #f5f7f9; }}
  .row-conflict {{ background: #fff5f5; }}
  ul.findings {{ margin: 0; padding-left: 0; list-style: none; line-height: 1.9; }}
  .tag {{ display: inline-block; min-width: 64px; text-align: center; padding: 2px 8px;
          border-radius: 4px; font-size: 12px; margin-right: 8px; }}
  .tag-gap {{ background: #f3e8ff; color: #7b3fd4; }}
  .tag-overlap, .tag-reversed_interval {{ background: #fdecec; color: #c0392b; }}
  .tag-out_of_order {{ background: #fff6e0; color: #9a6b00; }}
  .tag-bad_time {{ background: #eef1f4; color: #52606d; }}
  .open {{ color: #b7791f; font-weight: 600; }}
</style>
</head>
<body>
  <div class="card">
    <h1>样本交接链路审计报告</h1>
    <div class="meta">
      样本编号：<b>{html.escape(sample['code'])}</b>　·　样本名称：{html.escape(sample['name'])}　·
      生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
    <p><span class="status {status_class}">{STATUS_TEXT.get(audit['status'], audit['status'])}</span></p>
    <div class="conclusion">{html.escape(audit['conclusion'])}</div>
  </div>
  <div class="card">
    <h2>统计</h2>
    <table>
      <tr><th>交接记录</th><th>有效记录</th><th>断档</th><th>重叠</th><th>起止倒置</th>
          <th>逆序录入</th><th>未归还</th></tr>
      <tr><td>{s['total']}</td><td>{s['valid']}</td><td>{s['gaps']}</td><td>{s['overlaps']}</td>
          <td>{s['reversed']}</td><td>{s['out_of_order']}</td><td>{s['open_holds']}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>审计发现</h2>
    <ul class="findings">{finding_rows}</ul>
  </div>
  <div class="card">
    <h2>交接事件（按起始时间排序）</h2>
    <table>
      <tr><th>#</th><th>录入序</th><th>经手人</th><th>起始时间</th><th>结束时间</th>
          <th>保管时长</th><th>备注</th></tr>
      {event_rows or '<tr><td colspan="7" class="muted">暂无交接事件。</td></tr>'}
    </table>
  </div>
  <div class="card">
    <h2>结论签署</h2>
    <table>
      <tr><th>签署人</th><th>签署时间</th><th>状态</th><th>签署时审计结论</th>
          <th>快照校验值</th><th>备注</th><th>撤销信息</th></tr>
      {signoff_rows}
    </table>
  </div>
</body>
</html>
"""


def build_json(
    sample: dict[str, Any],
    audit: dict[str, Any],
    signoffs: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {
            "report": "样本交接链路审计报告",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sample": sample,
            "audit": audit,
            "signoffs": signoffs or [],
        },
        ensure_ascii=False,
        indent=2,
    )
