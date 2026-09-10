function shouldUpdateRefreshLedger({ status, publishStatus, targetKey, publishConfirmed }) {
  return status === "success"
    && publishStatus === "published"
    && publishConfirmed === true
    && Boolean(targetKey)
    && targetKey !== "manual";
}

export { shouldUpdateRefreshLedger };
