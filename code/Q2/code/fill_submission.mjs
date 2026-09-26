import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve("# D-项目文件夹-LW/Solution");
const template = path.join(root, "结果提交模板.xlsx");
const resultDir = path.join(root, "Q2", "results");
const output = path.join(resultDir, "Q2_结果提交.xlsx");

function csvRows(content) {
  content = content.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < content.length; i++) {
    const ch = content[i];
    if (ch === '"') {
      if (quoted && content[i + 1] === '"') { field += '"'; i++; }
      else quoted = !quoted;
    } else if (ch === "," && !quoted) {
      row.push(field); field = "";
    } else if ((ch === "\n" || ch === "\r") && !quoted) {
      if (ch === "\r" && content[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some(x => x !== "")) rows.push(row);
      row = [];
    } else field += ch;
  }
  row.push(field);
  if (row.some(x => x !== "")) rows.push(row);
  const [headers, ...body] = rows;
  return body.map(cells => Object.fromEntries(headers.map((key, i) => [key, cells[i] ?? ""])));
}

const trips = csvRows(await fs.readFile(path.join(resultDir, "trip_summary.csv"), "utf8"))
  .sort((a, b) => Number(a.start_time_s) - Number(b.start_time_s) || a.trip_id.localeCompare(b.trip_id));
const deliveries = csvRows(await fs.readFile(path.join(resultDir, "delivery_timeline.csv"), "utf8"))
  .sort((a, b) => Number(a.delivery_time_s) - Number(b.delivery_time_s) || a.box_id.localeCompare(b.box_id));
if (trips.length !== 19 || deliveries.length !== 80 || new Set(deliveries.map(x => x.box_id)).size !== 80) {
  throw new Error(`Unexpected source counts: trips=${trips.length}, deliveries=${deliveries.length}`);
}
const tripIds = new Set(trips.map(x => x.trip_id));
if (deliveries.some(x => !tripIds.has(x.trip_id))) throw new Error("Delivery references an unknown trip");

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(template));
const s2 = workbook.worksheets.getItem("Q2_运输架次");
const s3 = workbook.worksheets.getItem("Q2_逐箱交付");

// Copy the template's blank data-row style, then write only the two requested tables.
for (let r = 3; r <= 20; r++) s2.getRange(`A${r}:H${r}`).copyFrom(s2.getRange("A2:H2"), "all");
for (let r = 3; r <= 81; r++) s3.getRange(`A${r}:D${r}`).copyFrom(s3.getRange("A2:D2"), "all");
for (const area of [s2.getRange("A4:H20"), s3.getRange("A4:D81")]) {
  area.format.font = {name:"Times New Roman", size:11};
  area.format.horizontalAlignment = "center";
  area.format.wrapText = true;
}
s2.getRange("A2:H20").values = trips.map(t => [
  t.trip_id, t.vehicle_id, t.vehicle_type, t.battery_id,
  Number(t.start_time_s), t.route.split("->").slice(1, -1).join("→"),
  Number(t.end_time_s), Number(t.energy_kwh),
]);
s3.getRange("A2:D81").values = deliveries.map(d => [
  d.box_id, d.trip_id, d.service_node, Number(d.delivery_time_s),
]);
s2.getRange("E2:E20").setNumberFormat("0.000000");
s2.getRange("G2:H20").setNumberFormat("0.000000");
s3.getRange("D2:D81").setNumberFormat("0.000000");
workbook.recalculate();

for (const [sheet, range] of [["Q2_运输架次", "A1:H4"], ["Q2_逐箱交付", "A1:D4"]]) {
  const check = await workbook.inspect({kind:"table", range:`'${sheet}'!${range}`, include:"values,formulas", tableMaxRows:4, tableMaxCols:8});
  console.log(check.ndjson);
}
const errorScan = await workbook.inspect({kind:"match", searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options:{useRegex:true,maxResults:100}, summary:"formula error scan"});
console.log(errorScan.ndjson);

for (const [sheet, range, name] of [["Q2_运输架次", "A1:H9", "q2_sheet2_preview.png"], ["Q2_逐箱交付", "A1:D9", "q2_sheet3_preview.png"]]) {
  const preview = await workbook.render({sheetName:sheet, range, scale:1.5, format:"png"});
  await fs.writeFile(path.join(resultDir, name), new Uint8Array(await preview.arrayBuffer()));
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(output);
console.log(JSON.stringify({output, tripRows:trips.length, deliveryRows:deliveries.length}));
