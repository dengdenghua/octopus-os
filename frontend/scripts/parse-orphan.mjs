let d = "";
process.stdin
  .on("data", (c) => (d += c))
  .on("end", () => {
    d = d.replace(/^[\uFEFF\u200B]+/g, "");
    try {
      const o = JSON.parse(d);
      console.log("=== SUMMARY ===");
      console.log(JSON.stringify(o.summary, null, 2));
      console.log("=== ORPHAN FILES (first 30) ===");
      console.log(o.summary.orphanFiles.slice(0, 30).join("\n"));
      console.log(`\n(total orphan files: ${o.summary.orphanFiles.length})`);
      const reached = o.rows.filter((r) => r.isReached);
      console.log(
        `\n=== UNUSED EXPORTS IN REACHED FILES (${reached.length}) ===`,
      );
      console.log(
        reached
          .slice(0, 20)
          .map((r) => `${r.file}:${r.name} (${r.kind})`)
          .join("\n"),
      );
    } catch (e) {
      console.error("parse fail:", e.message, "head:", d.slice(0, 200));
    }
  });
