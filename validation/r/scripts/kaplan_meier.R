# Kaplan-Meier survival estimate with 95% confidence intervals (log-log), median
# survival with its confidence interval, and numbers at risk.
#
# Curvis: curvis.analysis.survival.kaplan_meier.
#
# Definitions:
# - Input: follow-up times (>= 0) and events (1 = event, 0 = censored). A subject with a
#   missing time or event (NaN) is left out, as survfit's na.action does.
# - Times that differ only by rounding are one time (survfit's default timefix = TRUE,
#   survival:::aeqSurv): distinct times whose gap is at most sqrt(.Machine$double.eps),
#   absolutely or relative to the mean of the distinct times, become the smaller one.
# - The estimate (survival::survfit(Surv(time, event) ~ 1)), at each distinct time t_j
#   (events and censorings, in increasing order):
#     n_j = number with time >= t_j (at risk), d_j = events at t_j, c_j = censored at t_j,
#     S(t_j) = prod_{i <= j} (1 - d_i / n_i).
#   A censoring at the same time as an event counts as at risk for that event.
# - Greenwood: sigma_j^2 = sum_{i <= j} d_i / (n_i (n_i - d_i)), the variance of
#   log S (survfit's std.err is sigma). The standard error of S is S * sigma (as
#   summary.survfit reports it). After an event that empties the risk set (d = n) S is 0
#   and sigma is infinite or undefined.
# - 95% confidence interval, conf.type = "log-log" (lifelines' interval, chosen for
#   Curvis by the project owner): with z = qnorm(0.975),
#     lower = S^exp(-z sigma / log S),  upper = S^exp( z sigma / log S).
#   (log S < 0, so the exponent of the lower limit is above 1 and the limit below S.)
#   Where S is 0 or 1 survfit gives no interval from this formula; the fixtures record
#   whatever survfit returns there (NA becomes "NaN").
# - Median survival (quantile.survfit, probs = 0.5): the first time S(t) <= 0.5. Where S
#   equals 0.5 (within sqrt(.Machine$double.eps)) over an interval, the midpoint of that
#   interval and the next time S drops. Its 95% CI applies the same rule to the lower and
#   upper confidence curves. NaN where a curve never reaches 0.5 ("not reached").
# - Numbers at risk at chosen times (summary.survfit(times = , extend = TRUE)): the number
#   with time >= t, for the risk table under a survival graph.
#
# The script computes S, sigma and the interval by the formulas above and stops unless
# they agree with survfit to 1e-12 (where survfit gives a finite value).
#
# Inputs: time, event (arrays, NaN for missing), risk_times (times for numbers at risk).
# Writes kaplan_meier/<case>.

Z <- qnorm(0.975)

km_case <- function(case, description, time, event, risk_times = NULL) {
  time <- as.double(time)
  event <- as.double(event)
  stopifnot(length(time) == length(event),
            all(is.na(time) | time >= 0),
            all(is.na(event) | event %in% c(0, 1)))
  keep <- !is.na(time) & !is.na(event)
  stopifnot(any(keep))
  t_ok <- time[keep]
  e_ok <- event[keep]
  if (is.null(risk_times)) {
    risk_times <- pretty(c(0, max(t_ok)), n = 5)
  }
  risk_times <- as.double(risk_times)

  fit <- survival::survfit(survival::Surv(t_ok, e_ok) ~ 1, conf.type = "log-log",
                           conf.int = 0.95)

  # The estimate by the formulas, as a check of the definitions.
  t_fix <- survival::aeqSurv(survival::Surv(t_ok, e_ok))[, 1]
  times <- sort(unique(t_fix))
  n_risk <- vapply(times, function(t) sum(t_fix >= t), numeric(1))
  n_event <- vapply(times, function(t) sum(t_fix == t & e_ok == 1), numeric(1))
  n_censor <- vapply(times, function(t) sum(t_fix == t & e_ok == 0), numeric(1))
  surv <- cumprod(1 - n_event / n_risk)
  sigma <- sqrt(cumsum(n_event / (n_risk * (n_risk - n_event))))
  stopifnot(
    isTRUE(all.equal(fit$time, times, tolerance = 0)),
    isTRUE(all.equal(fit$n.risk, n_risk, tolerance = 0)),
    isTRUE(all.equal(fit$n.event, n_event, tolerance = 0)),
    isTRUE(all.equal(fit$n.censor, n_censor, tolerance = 0)),
    isTRUE(all.equal(fit$surv, surv, tolerance = 1e-12))
  )
  finite <- is.finite(fit$std.err) & surv > 0
  stopifnot(isTRUE(all.equal(fit$std.err[finite], sigma[finite], tolerance = 1e-12)))
  inner <- finite & surv < 1 & is.finite(fit$lower) & is.finite(fit$upper)
  if (any(inner)) {
    s <- surv[inner]
    lower <- s^exp(-Z * sigma[inner] / log(s))
    upper <- s^exp(Z * sigma[inner] / log(s))
    stopifnot(isTRUE(all.equal(fit$lower[inner], lower, tolerance = 1e-12)),
              isTRUE(all.equal(fit$upper[inner], upper, tolerance = 1e-12)))
  }

  med <- quantile(fit, probs = 0.5, conf.int = TRUE)
  at_risk <- summary(fit, times = risk_times, extend = TRUE)
  stopifnot(isTRUE(all.equal(at_risk$time, risk_times, tolerance = 0)))

  write_fixture(
    analysis = "kaplan_meier", case = case,
    description = paste0(description, " Kaplan-Meier, 95% CI log-log."),
    inputs = list(time = time, event = event, risk_times = risk_times),
    expected = list(
      n = scalar(as.integer(length(t_ok))),
      n_events = scalar(as.integer(sum(e_ok))),
      time = fit$time,
      n_risk = as.integer(fit$n.risk),
      n_event = as.integer(fit$n.event),
      n_censor = as.integer(fit$n.censor),
      survival = fit$surv,
      std_err = fit$surv * fit$std.err,
      lower = fit$lower,
      upper = fit$upper,
      median = scalar(unname(med$quantile)),
      median_lower = scalar(unname(med$lower)),
      median_upper = scalar(unname(med$upper)),
      risk_n = as.integer(at_risk$n.risk)
    ),
    kinds = c(n = "exact", n_events = "exact", time = "exact", n_risk = "exact",
              n_event = "exact", n_censor = "exact", survival = "statistic",
              std_err = "statistic", lower = "ci", upper = "ci", median = "statistic",
              median_lower = "ci", median_upper = "ci", risk_n = "exact"),
    packages = c("survival", "stats")
  )
}

# --- Cases -------------------------------------------------------------------------

km_case("basic", "20 subjects, distinct times, 6 censored.",
        time = round(rexp(20, 1 / 12), 2),
        event = c(1, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0))

km_case("ties", "Tied times: several events at one time, and events and censorings at one time.",
        time = c(2, 3, 3, 3, 5, 5, 6, 8, 8, 8, 8, 10, 12, 12, 15),
        event = c(1, 1, 1, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0, 1, 0))

km_case("all_censored", "Every subject is censored: S = 1, the median is not reached.",
        time = c(3, 5, 7, 9, 11, 14),
        event = c(0, 0, 0, 0, 0, 0))

km_case("single_event", "One event among censored subjects.",
        time = c(2, 4, 6, 8, 10, 12, 14),
        event = c(0, 0, 1, 0, 0, 0, 0))

km_case("last_is_event", "The longest time is an event: S drops to 0.",
        time = c(1, 3, 4, 6, 9, 13),
        event = c(1, 0, 1, 1, 0, 1))

km_case("last_is_censored", "The longest time is censored: S stays above 0.",
        time = c(1, 3, 4, 6, 9, 13),
        event = c(1, 1, 0, 1, 1, 0))

km_case("all_events_same_time", "Constant data: every subject has an event at the same time.",
        time = c(5, 5, 5, 5),
        event = c(1, 1, 1, 1))

km_case("n_two", "n = 2, both events.", time = c(3, 7), event = c(1, 1))

km_case("n_two_censored", "n = 2, one event, one censored later.",
        time = c(3, 7), event = c(1, 0))

km_case("missing", "Missing times and events: those subjects are left out.",
        time = c(2, NaN, 4, 5, 7, 8, NaN, 11, 13, 16),
        event = c(1, 1, NaN, 1, 0, 1, 0, NaN, 1, 0))

km_case("time_zero", "Events and a censoring at time 0.",
        time = c(0, 0, 0, 2, 4, 5, 7, 9),
        event = c(1, 1, 0, 1, 0, 1, 1, 0))

km_case("median_flat", "S is exactly 0.5 over an interval: the median is a midpoint.",
        time = c(1, 2, 3, 4, 5, 6, 7, 8),
        event = c(1, 1, 1, 1, 1, 1, 1, 1))

km_case("median_ci_open", "Few events: the upper limit of the median is not reached.",
        time = c(2, 3, 5, 8, 9, 12, 15, 18, 20, 24),
        event = c(1, 1, 1, 0, 1, 1, 0, 0, 0, 0))

km_case("large", "200 subjects, times rounded to whole days: many ties, 30% censored.",
        time = round(rweibull(200, shape = 1.3, scale = 40)),
        event = rbinom(200, 1, 0.7),
        risk_times = c(0, 20, 40, 60, 80, 100, 120))

km_case("near_ties", "Times that differ only by rounding (0.1 + 0.2 and 0.3, 1 and 1 + 1e-10) are one time.",
        time = c(0.1 + 0.2, 0.3, 1, 1 + 1e-10, 2, 2.5, 3 * (1 + 1e-9), 3, 5, 6),
        event = c(1, 0, 1, 1, 0, 1, 1, 1, 0, 1))
