import { createElement } from "react"

export const APP_VERSION = typeof __LABEL_LAB_VERSION__ === "string" ? __LABEL_LAB_VERSION__ : "0.2.0"
export const BUILD_SHA = typeof __LABEL_LAB_BUILD_SHA__ === "string" ? __LABEL_LAB_BUILD_SHA__ : "dev"

export function formatAppVersion(version = APP_VERSION, sha = BUILD_SHA) {
  const build = sha.trim() ? sha.trim().slice(0, 7) : "dev"
  return `Label System v${version} · build ${build}`
}

export function AppVersion() {
  return createElement("span", { className: "font-data text-[0.68rem] text-[var(--muted)]" }, formatAppVersion())
}
