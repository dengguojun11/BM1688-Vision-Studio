#!/usr/bin/env bash
# shellcheck shell=bash

labelu_deploy_is_frontend_only_path() {
  local path="${1:-}"
  [[ -n "${path}" ]] || return 1

  case "${path}" in
    frontend/labelU-Kit/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

labelu_deploy_changes_are_frontend_only() {
  local path saw_change=0
  for path in "$@"; do
    [[ -n "${path}" ]] || continue
    saw_change=1
    if ! labelu_deploy_is_frontend_only_path "${path}"; then
      return 1
    fi
  done

  [[ "${saw_change}" == "1" ]]
}
