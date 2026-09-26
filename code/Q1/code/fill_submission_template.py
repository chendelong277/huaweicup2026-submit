from __future__ import annotations

import csv
import shutil
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT.parent / "结果提交模板.xlsx"
OUT = ROOT / "Q1_结果提交模板_已填写.xlsx"
RESULTS = ROOT / "results"


def read_csv(name: str) -> list[dict[str, str]]:
    with (RESULTS / name).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    wb = load_workbook(TEMPLATE)
    if "Sheet1" not in wb.sheetnames:
        raise ValueError(f"Template sheets: {wb.sheetnames}; Sheet1 is required")
    ws = wb["Sheet1"]
    # Preserve all template worksheets and fill only the existing Sheet1 layout.
    # The template is a 20-row x 16-column form with two blocks:
    # rows 1-6: maximum safe payload; rows 10-20: batching records.
    if ws.max_row < 20 or ws.max_column < 16:
        raise ValueError(f"Unexpected Sheet1 dimensions: {ws.max_row}x{ws.max_column}")

    safe = read_csv("safe_payload.csv")
    batches = read_csv("batching_baseline.csv")
    safe_by_vehicle = {v: [r for r in safe if r["vehicle_type"] == v] for v in ["A", "B", "C"]}

    # Safe-payload block: columns A:D, rows 2:6. Keep merged headers and formatting.
    for row_idx, vehicle_type in enumerate(["A", "B", "C"], start=2):
        rows = sorted(safe_by_vehicle[vehicle_type], key=lambda r: r["service_node"])
        payload_text = "；".join(
            f"{r['service_node']}:{float(r['max_safe_payload_kg']):.3f} kg" if r["max_safe_payload_kg"] not in ("", "None") else f"{r['service_node']}:不可行"
            for r in rows
        )
        # Existing template uses a wide merged result cell in column D.
        ws.cell(row_idx, 1).value = f"{vehicle_type}型"
        ws.cell(row_idx, 2).value = payload_text
        ws.cell(row_idx, 3).value = "20%"
        ws.cell(row_idx, 4).value = "最大安全载荷（按服务区列示）"

    # Add an explicit compact note below the original maximum-payload block.
    ws.cell(7, 1).value = "说明"
    ws.cell(7, 2).value = "默认返航安全余量20%；最大安全载荷同时受额定载荷、往返能量和装载体积约束，具体货箱批次另见结果附件。"
    ws.merge_cells(start_row=7, start_column=2, end_row=7, end_column=16)

    # Batching block: rows 10:20 are the template's compact summary area.
    # Preserve the 10-row template body and place the complete 18-batch detail in a second sheet,
    # while Sheet1 keeps its intended concise submission view.
    ws.cell(9, 1).value = "Q1主方案汇总"
    ws.merge_cells(start_row=9, start_column=1, end_row=9, end_column=16)
    summary_headers = ["批次", "服务区", "机型", "箱数", "质量kg", "体积m³", "能耗kWh", "作业时间s"]
    for j, h in enumerate(summary_headers, start=1):
        ws.cell(10, j).value = h
    for i, row in enumerate(read_csv("batch_summary.csv")[:10], start=11):
        vals = [
            row["batch_id"], row["service_node"], row["vehicle_type"], int(row["box_count"]),
            float(row["batch_total_mass_kg"]), float(row["batch_total_volume_m3"]),
            float(row["batch_energy_kwh"]), float(row["operation_time_s"]),
        ]
        for j, value in enumerate(vals, start=1):
            ws.cell(i, j).value = value

    # Add the full detail and machine-readable interface without changing Sheet1's layout.
    detail_name = "Q1_Batching_Detail"
    if detail_name in wb.sheetnames:
        del wb[detail_name]
    detail = wb.create_sheet(detail_name)
    detail_headers = list(batches[0].keys())
    detail.append(detail_headers)
    for row in batches:
        detail.append([row[h] for h in detail_headers])

    # A concise audit sheet records the actual Q1 summary and verification status.
    audit_name = "Q1_Audit"
    if audit_name in wb.sheetnames:
        del wb[audit_name]
    audit = wb.create_sheet(audit_name)
    audit.append(["指标", "结果"])
    audit.append(["货箱总数", 80])
    audit.append(["主方案架次", 18])
    audit.append(["主方案总能耗kWh", 59.26390830535006])
    audit.append(["主方案累计作业时间s", 32804.87341936896])
    audit.append(["货箱唯一配送", "通过"])
    audit.append(["批次硬约束审计", "通过"])
    audit.append(["返航余量敏感性", "10%-35%可行；40%不可行"])

    # Consistent professional formatting, while retaining template fills and dimensions on Sheet1.
    for sheet in [ws, detail, audit]:
        for row in sheet.iter_rows():
            for cell in row:
                cell.font = cell.font.copy(name="Arial")
                cell.alignment = cell.alignment.copy(vertical="center", wrap_text=True)
        sheet.freeze_panes = "A2"
    for sheet in [detail, audit]:
        for cell in sheet[1]:
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for col in sheet.columns:
            letter = col[0].column_letter
            width = min(42, max(12, max(len(str(c.value or "")) for c in col) + 2))
            sheet.column_dimensions[letter].width = width
    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            ws.cell(row, col).alignment = Alignment(vertical="center", wrap_text=True)
    for col in range(1, 9):
        ws.column_dimensions[chr(64 + col)].width = max(ws.column_dimensions[chr(64 + col)].width or 0, [13, 13, 9, 8, 10, 10, 12, 14][col - 1])
    for col in range(5, 9):
        for row in range(11, 21):
            ws.cell(row, col).number_format = "0.000"
    wb.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
