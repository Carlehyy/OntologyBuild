#!/usr/bin/env bash
set -Eeuo pipefail

validate_deploy_app_dir() {
  local app_dir="${1-}"
  local relative

  if [ -z "$app_dir" ]; then
    printf 'DEPLOY_APP_DIR must not be empty\n' >&2
    return 1
  fi
  case "$app_dir" in
    /*) ;;
    *)
      printf 'DEPLOY_APP_DIR must be an absolute path\n' >&2
      return 1
      ;;
  esac
  if [ "$app_dir" = "/" ]; then
    printf 'DEPLOY_APP_DIR must not be the filesystem root\n' >&2
    return 1
  fi
  case "$app_dir" in
    *[!A-Za-z0-9._/-]*)
      printf 'DEPLOY_APP_DIR contains unsupported characters\n' >&2
      return 1
      ;;
    *//*|*/)
      printf 'DEPLOY_APP_DIR must be normalized without duplicate or trailing slashes\n' >&2
      return 1
      ;;
    */./*|*/../*|*/.|*/..)
      printf 'DEPLOY_APP_DIR must not contain dot path segments\n' >&2
      return 1
      ;;
  esac

  relative="${app_dir#/}"
  case "$relative" in
    */*) ;;
    *)
      printf 'DEPLOY_APP_DIR must be below a top-level directory\n' >&2
      return 1
      ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  validate_deploy_app_dir "${1-}"
fi
