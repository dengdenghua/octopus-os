#!/usr/bin/env bash
# Shared optional Secure Boot build/runtime arguments.

configure_echo_secure_boot() {
  ECHO_MKOSI_SECURE_BOOT_ARGS=()
  ECHO_MKOSI_SECURE_BOOT_RUNTIME_ARGS=()
  ECHO_SECURE_BOOT_CONFIGURED=no

  local key_path="${ECHO_SECURE_BOOT_KEY:-}"
  local certificate_path="${ECHO_SECURE_BOOT_CERTIFICATE:-}"
  local pcr_key_path="${ECHO_PCR_POLICY_KEY:-}"
  local pcr_certificate_path="${ECHO_PCR_POLICY_CERTIFICATE:-}"
  local pcr_public_key_path="${ECHO_TPM2_PCR_PUBLIC_KEY:-}"
  if [[ -z "$key_path" && -z "$certificate_path" && \
        -z "$pcr_key_path" && -z "$pcr_certificate_path" && \
        -z "$pcr_public_key_path" ]]; then
    return 0
  fi
  if [[ -z "$key_path" || -z "$certificate_path" ]]; then
    echo "ECHO_SECURE_BOOT_KEY and ECHO_SECURE_BOOT_CERTIFICATE must be supplied together" >&2
    return 2
  fi
  if [[ -z "$pcr_key_path" || -z "$pcr_certificate_path" || \
        -z "$pcr_public_key_path" ]]; then
    echo "ECHO_PCR_POLICY_KEY, ECHO_PCR_POLICY_CERTIFICATE and ECHO_TPM2_PCR_PUBLIC_KEY must be supplied together" >&2
    return 2
  fi
  for command_name in cmp openssl realpath stat; do
    command -v "$command_name" >/dev/null 2>&1 || {
      echo "Secure Boot dependency missing: $command_name" >&2
      return 1
    }
  done

  key_path="$(realpath "$key_path")"
  certificate_path="$(realpath "$certificate_path")"
  pcr_key_path="$(realpath "$pcr_key_path")"
  pcr_certificate_path="$(realpath "$pcr_certificate_path")"
  pcr_public_key_path="$(realpath "$pcr_public_key_path")"
  [[ -f "$key_path" && ! -L "$key_path" && -r "$key_path" ]] || {
    echo "Secure Boot private key is missing or unreadable: $key_path" >&2
    return 1
  }
  [[ -f "$certificate_path" && ! -L "$certificate_path" && \
     -r "$certificate_path" ]] || {
    echo "Secure Boot certificate is missing or unreadable: $certificate_path" >&2
    return 1
  }
  [[ -f "$pcr_key_path" && ! -L "$pcr_key_path" && -r "$pcr_key_path" ]] || {
    echo "PCR policy private key is missing or unreadable: $pcr_key_path" >&2
    return 1
  }
  [[ -f "$pcr_certificate_path" && ! -L "$pcr_certificate_path" && \
     -r "$pcr_certificate_path" ]] || {
    echo "PCR policy certificate is missing or unreadable: $pcr_certificate_path" >&2
    return 1
  }
  [[ -f "$pcr_public_key_path" && ! -L "$pcr_public_key_path" && \
     -r "$pcr_public_key_path" ]] || {
    echo "TPM2 PCR public key is missing or unreadable: $pcr_public_key_path" >&2
    return 1
  }

  local private_key private_label key_owner key_mode
  for private_key in "$key_path" "$pcr_key_path"; do
    if [[ "$private_key" == "$key_path" ]]; then
      private_label="Secure Boot"
    else
      private_label="PCR policy"
    fi
    key_owner="$(stat -c '%u' "$private_key")"
    key_mode="$(stat -c '%a' "$private_key")"
    [[ "$key_owner" == "$(id -u)" ]] || {
      echo "$private_label private key must be owned by the build user" >&2
      return 1
    }
    if [[ "$key_mode" != 400 && "$key_mode" != 600 ]]; then
      echo "$private_label private key mode must be 0400 or 0600, found $key_mode" >&2
      return 1
    fi
  done

  local public_file public_label public_owner public_mode
  for public_file in \
    "$certificate_path" "$pcr_certificate_path" "$pcr_public_key_path"; do
    case "$public_file" in
      "$certificate_path") public_label="Secure Boot certificate" ;;
      "$pcr_certificate_path") public_label="PCR policy certificate" ;;
      *) public_label="TPM2 PCR public key" ;;
    esac
    public_owner="$(stat -c '%u' "$public_file")"
    public_mode="$(stat -c '%a' "$public_file")"
    [[ "$public_owner" == "$(id -u)" ]] || {
      echo "$public_label must be owned by the build user" >&2
      return 1
    }
    (( (8#$public_mode & 8#022) == 0 )) || {
      echo "$public_label must not be group/other writable" >&2
      return 1
    }
  done

  openssl x509 -in "$certificate_path" -noout >/dev/null 2>&1 || {
    echo "Secure Boot certificate is not a valid X.509 certificate" >&2
    return 1
  }
  openssl x509 -in "$pcr_certificate_path" -noout >/dev/null 2>&1 || {
    echo "PCR policy certificate is not a valid X.509 certificate" >&2
    return 1
  }
  openssl rsa -pubin -in "$pcr_public_key_path" -noout >/dev/null 2>&1 || {
    echo "TPM2 PCR public key must be a PEM-encoded RSA public key" >&2
    return 1
  }
  cmp -s \
    <(openssl pkey -in "$key_path" -pubout -outform DER) \
    <(openssl x509 -in "$certificate_path" -pubkey -noout | \
      openssl pkey -pubin -outform DER) || {
    echo "Secure Boot key and certificate do not match" >&2
    return 1
  }
  cmp -s \
    <(openssl pkey -in "$pcr_key_path" -pubout -outform DER) \
    <(openssl x509 -in "$pcr_certificate_path" -pubkey -noout | \
      openssl pkey -pubin -outform DER) || {
    echo "PCR policy key and certificate do not match" >&2
    return 1
  }
  cmp -s \
    <(openssl pkey -in "$pcr_key_path" -pubout -outform DER) \
    <(openssl pkey -pubin -in "$pcr_public_key_path" -outform DER) || {
    echo "TPM2 PCR public key does not match the PCR policy private key" >&2
    return 1
  }

  ECHO_SECURE_BOOT_KEY="$key_path"
  ECHO_SECURE_BOOT_CERTIFICATE="$certificate_path"
  ECHO_PCR_POLICY_KEY="$pcr_key_path"
  ECHO_PCR_POLICY_CERTIFICATE="$pcr_certificate_path"
  ECHO_TPM2_PCR_PUBLIC_KEY="$pcr_public_key_path"
  ECHO_SECURE_BOOT_CONFIGURED=yes
  export ECHO_SECURE_BOOT_KEY ECHO_SECURE_BOOT_CERTIFICATE
  export ECHO_PCR_POLICY_KEY ECHO_PCR_POLICY_CERTIFICATE
  export ECHO_TPM2_PCR_PUBLIC_KEY ECHO_SECURE_BOOT_CONFIGURED
  ECHO_MKOSI_SECURE_BOOT_ARGS=(
    --secure-boot=yes
    --secure-boot-key="$key_path"
    --secure-boot-certificate="$certificate_path"
    --verity=yes
    --verity-key="$key_path"
    --verity-certificate="$certificate_path"
    --sign-expected-pcr=yes
    --sign-expected-pcr-key="$pcr_key_path"
    --sign-expected-pcr-certificate="$pcr_certificate_path"
  )
  # shellcheck disable=SC2034 # Consumed by the VM smoke scripts that source this helper.
  ECHO_MKOSI_SECURE_BOOT_RUNTIME_ARGS=(
    "${ECHO_MKOSI_SECURE_BOOT_ARGS[@]}"
    --firmware=uefi-secure-boot
    --firmware-variables=custom
  )
}
