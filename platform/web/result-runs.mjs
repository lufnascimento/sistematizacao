export function selectResultRun(runs, requestedId) {
  if (requestedId) return runs.find((run) => run.id === requestedId) ?? null;
  return runs.find((run) => run.status === "SUCCEEDED") ?? runs[0] ?? null;
}
