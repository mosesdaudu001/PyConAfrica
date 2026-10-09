# The configuration matrix. Each entry sets only the four knobs the brief varies.
# Nothing else about the run may change.
#
#   Set A (cumulative)          Set B (one change at a time, each applied to C0)
#   C0 baseline                 B1 async driver only
#   C1 + async driver           B2 pgbouncer only
#   C2 + pgbouncer              B3 8 workers only
#   C3 + 8 workers              B4 redis only
#   C4 + redis
#
# Note: B1 is by construction identical to C1. It is still run separately; the
# two give six independent replicates of the same configuration.

config_env() {
  case "$1" in
    C0) D=sync  T=pg        C=off W=1 ;;
    C1) D=async T=pg        C=off W=1 ;;
    C2) D=async T=pgbouncer C=off W=1 ;;
    C3) D=async T=pgbouncer C=off W=8 ;;
    C4) D=async T=pgbouncer C=on  W=8 ;;
    B1) D=async T=pg        C=off W=1 ;;
    B2) D=sync  T=pgbouncer C=off W=1 ;;
    B3) D=sync  T=pg        C=off W=8 ;;
    B4) D=sync  T=pg        C=on  W=1 ;;
    *)  echo "unknown config: $1" >&2; return 1 ;;
  esac
  export BENCH_DRIVER="$D" BENCH_TARGET="$T" BENCH_CACHE="$C" BENCH_WORKERS="$W"
}
ALL_CONFIGS="C0 C1 C2 C3 C4 B1 B2 B3 B4"
