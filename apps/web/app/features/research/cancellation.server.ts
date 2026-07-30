const activeRuns = new Map<string, AbortController>();

export function registerRun(turnId: string, controller: AbortController) {
  activeRuns.set(turnId, controller);
}

export function unregisterRun(turnId: string) {
  activeRuns.delete(turnId);
}

export function cancelRegisteredRun(turnId: string) {
  const controller = activeRuns.get(turnId);
  if (!controller) {
    return false;
  }
  controller.abort();
  activeRuns.delete(turnId);
  return true;
}
