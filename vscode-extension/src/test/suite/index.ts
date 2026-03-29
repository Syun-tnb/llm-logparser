import * as fs from "fs";
import * as path from "path";
import Mocha from "mocha";

export async function run(): Promise<void> {
  const mocha = new Mocha({
    ui: "tdd",
    color: true,
    timeout: 20000,
  });

  const testsRoot = __dirname;
  for (const entry of fs.readdirSync(testsRoot)) {
    if (entry === "index.js" || !entry.endsWith(".test.js")) {
      continue;
    }
    mocha.addFile(path.join(testsRoot, entry));
  }

  await new Promise<void>((resolve, reject) => {
    mocha.run((failures) => {
      if (failures > 0) {
        reject(new Error(`${failures} test(s) failed.`));
        return;
      }
      resolve();
    });
  });
}
