import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["smoke"],
  description:
    "Discover first, then start_contribution in dry-run — quality gate before ship.",
  async test(t) {
    await t.send(
      [
        "Run an OoCoder contribution smoke path:",
        "1) Call discover_issues with limit 3.",
        "2) Then call start_contribution for https://github.com/octocat/Hello-World/issues/1 with dryRun=true.",
        "Do not open a real PR.",
      ].join(" "),
    );
    t.succeeded();
    t.calledTool("discover_issues");
    t.calledTool("start_contribution");
  },
});
