// PostHog browser SDK loader and initializer.
//
// Lives as an external file (not inline in the template) so the production
// Content-Security-Policy (`script-src 'self' …`) allows it without a
// per-request nonce. Configuration is read from data-attributes on the
// <script> tag rendered by templates/_analytics_head.html.
//
// Behaviour notes (mirrored from the template that includes this file):
//   - api_host is /ph (this app's reverse proxy), not the PostHog cloud
//     host directly. Keeps cookies first-party and dodges ad-blockers.
//   - person_profiles: 'identified_only' means PostHog only creates a
//     person record when we explicitly identify() the user. Anonymous
//     visitors get bucketed into a single $$anon$$ profile.
//   - disable_session_recording defaults true; we only record once we
//     have a logged-in user.
(function () {
  // The script tag declares `data-posthog-init` so we can find ourselves
  // even when loaded with `defer` (where document.currentScript is null).
  var tag = document.querySelector('script[data-posthog-init]');
  if (!tag) {
    return;
  }

  var projectKey = tag.getAttribute('data-project-key');
  if (!projectKey) {
    return;
  }
  var apiHost = tag.getAttribute('data-api-host') || '/ph';
  var userId = tag.getAttribute('data-user-id') || '';
  var authProvider = tag.getAttribute('data-auth-provider') || 'email';

  // Standard PostHog snippet — fetches the SDK bundle from
  // <api_host>/static/array.js. With api_host="/ph", that resolves to
  // /ph/static/array.js, which our proxy forwards upstream.
  !function (t, e) {
    var o, n, p, r;
    e.__SV || (window.posthog = e, e._i = [], e.init = function (i, s, a) {
      function g(t, e) { var o = e.split('.'); 2 == o.length && (t = t[o[0]], e = o[1]); t[e] = function () { t.push([e].concat(Array.prototype.slice.call(arguments, 0))); }; }
      (p = t.createElement('script')).type = 'text/javascript';
      p.crossOrigin = 'anonymous';
      p.async = !0;
      p.src = s.api_host.replace('.i.posthog.com', '-assets.i.posthog.com') + '/static/array.js';
      (r = t.getElementsByTagName('script')[0]).parentNode.insertBefore(p, r);
      var u = e;
      for (void 0 !== a ? u = e[a] = [] : a = 'posthog', u.people = u.people || [], u.toString = function (t) { var e = 'posthog'; return 'posthog' !== a && (e += '.' + a), t || (e += ' (stub)'), e; }, u.people.toString = function () { return u.toString(1) + '.people (stub)'; }, o = 'init me ws ys ps bs capture je Di ks register register_once register_for_session unregister unregister_for_session Ps getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSurveysLoaded onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty Es $s createPersonProfile Is opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing Ss debug xs getPageViewId captureTraceFeedback captureTraceMetric'.split(' '), n = 0; n < o.length; n++) g(u, o[n]);
      e._i.push([i, s, a]);
    }, e.__SV = 1);
  }(document, window.posthog || []);

  window.posthog.init(projectKey, {
    api_host: apiHost,
    person_profiles: 'identified_only',
    capture_pageview: true,
    capture_pageleave: 'if_capture_pageview',
    autocapture: true,
    disable_session_recording: !userId
  });

  if (userId) {
    // Bind the anonymous distinct_id to the stable application user_id.
    // Server-side identify() also fires from the auth flow, but the
    // browser-side identify() is what surfaces the user in real-time
    // PostHog Live mode.
    window.posthog.identify(userId, { auth_provider: authProvider });
  }
})();
