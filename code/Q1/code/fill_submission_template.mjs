import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const solutionDir = "# D-项目文件夹-LW/Solution";
const q1Dir = `${solutionDir}/Q1`;
const templatePath = `${solutionDir}/结果提交模板.xlsx`;
const outputPath = `${q1Dir}/Q1_结果提交模板_已填写.xlsx`;

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").trim().split(/\r?\n/);
  const parseLine = (line) => {
    const out = [];
    let cur = "";
    let quoted = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (quoted && line[i + 1] === '"') { cur += '"'; i++; }
        else quoted = !quoted;
      } else if (ch === "," && !quoted) { out.push(cur); cur = ""; }
      else cur += ch;
    }
    out.push(cur);
    return out;
  };
  const headers = parseLine(lines[0]);
  return lines.slice(1).filter(Boolean).map((line) => {
    const values = parseLine(line);
    return Object.fromEntries(headers.map((h, i) => [h, values[i] ?? ""]));
  });
}

const summary = parseCsv(await fs.readFile(`${q1Dir}/results/batch_summary.csv`, "utf8"));
const details = parseCsv(await fs.readFile(`${q1Dir}/results/batching_baseline.csv`, "utf8"));
const boxLists = new Map();
for (const row of details) {
  if (!boxLists.has(row.batch_id)) boxLists.set(row.batch_id, []);
  boxLists.get(row.batch_id).push(row.box_id);
}

const input = await FileBlob.load(templatePath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheet = workbook.worksheets.getItemAt(0);
if (sheet.name !== "Q1_单点组批") throw new Error(`Unexpected first sheet: ${sheet.name}`);

const rows = summary.map((row) => [
  row.batch_id,
  row.service_node,
  row.vehicle_type,
  boxLists.get(row.batch_id).join("; "),
  Number(row.batch_total_mass_kg),
  Number(row.batch_total_volume_m3),
  Number(row.operation_time_s),
  Number(row.batch_energy_kwh),
  Number(row.soc_end) * 100,
]);
sheet.getRange(`A2:I${rows.length + 1}`).values = rows;
sheet.getRange(`A2:I${rows.length + 1}`).format = {
  font: { name: "Arial", size: 10, color: "#000000" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#D9D9D9" },
};
sheet.getRange(`D2:D${rows.length + 1}`).format.horizontalAlignment = "left";
sheet.getRange(`E2:E${rows.length + 1}`).format.numberFormat = "0.000";
sheet.getRange(`F2:F${rows.length + 1}`).format.numberFormat = "0.000";
sheet.getRange(`G2:G${rows.length + 1}`).format.numberFormat = "0.000";
sheet.getRange(`H2:H${rows.length + 1}`).format.numberFormat = "0.000";
sheet.getRange(`I2:I${rows.length + 1}`).format.numberFormat = "0.0";
sheet.getRange(`A2:I${rows.length + 1}`).format.rowHeight = 30;
sheet.getRange("A1:I1").format.font = { name: "Arial", size: 11, bold: true, color: "#000000" };
sheet.getRange("A1:I1").format.wrapText = true;
sheet.freezePanes.freezeRows(1);

workbook.recalculate();
const check = await workbook.inspect({ kind: "table", sheetId: "Q1_单点组批", range: "A1:I19", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 10, maxChars: 12000 });
console.log(check.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" });
console.log(errors.ndjson);
const preview = await workbook.render({ sheetName: "Q1_单点组批", autoCrop: "all", scale: 1.5, format: "png" });
await fs.writeFile(`${q1Dir}/q1_submission_preview.png`, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
