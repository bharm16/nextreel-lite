# Grafana Cloud Complete Observability Setup

This document provides step-by-step instructions to complete your Grafana Cloud integration for comprehensive monitoring of your NextReel application.

## 🎯 What You Get

- **Logs** → Loki (searchable, structured logs with 14 days retention)  
- **Metrics** → Prometheus (real-time application and system metrics)
- **Dashboards** → Grafana (visual monitoring with alerts)

## 📋 Prerequisites

✅ Packages installed: `python-logging-loki`, `prometheus-client`  
✅ Enhanced logging with Loki integration  
✅ Comprehensive Prometheus metrics collection  
✅ Metrics endpoint: `/metrics`  
✅ Dashboard configuration ready  

## 🚀 Setup Instructions

### Step 1: Create Grafana Cloud Account

1. Go to https://grafana.com/auth/sign-up/create-user
2. Sign up for a **free** Grafana Cloud account
3. Complete email verification

### Step 2: Get Your Credentials

1. Log into your Grafana Cloud portal
2. Go to **My Account** → **Cloud Portal**
3. Find your stack information:

#### For Loki (Logs):
```
URL: https://logs-prod-xxx.grafana.net
User: [your-user-id]
API Key: [your-loki-api-key]
```

#### For Prometheus (Metrics - Optional):
```
URL: https://prometheus-prod-xxx.grafana.net  
User: [your-user-id]
API Key: [your-prometheus-api-key]
```

### Step 3: Update Environment Variables

Add these to your `.env` file:

```bash
# Grafana Loki Configuration (Required)
GRAFANA_LOKI_URL=https://logs-prod-xxx.grafana.net
GRAFANA_LOKI_USER=your-loki-user-id
GRAFANA_LOKI_KEY=your-loki-api-key

# Prometheus Push Gateway (Optional - for remote metrics)
GRAFANA_PROMETHEUS_URL=https://prometheus-prod-xxx.grafana.net
GRAFANA_PROMETHEUS_USER=your-prometheus-user-id  
GRAFANA_PROMETHEUS_KEY=your-prometheus-api-key

# Application Version
APP_VERSION=1.0.0
```

### Step 4: Import Dashboard

1. In your Grafana Cloud instance, go to **Dashboards** → **Import**
2. Upload the `grafana-dashboard.json` file from this directory
3. Select your Prometheus data source when prompted

### Step 5: Verify Integration

1. Start your NextReel application:
   ```bash
   python3 app.py
   ```

2. Check logs are flowing to Loki:
   - Go to Grafana → Explore
   - Select Loki data source
   - Query: `{application="nextreel"}`

3. Check metrics endpoint:
   ```bash
   curl http://localhost:5000/metrics | grep nextreel
   ```

4. Verify dashboard shows data:
   - Go to your imported NextReel dashboard
   - Should show HTTP requests, database connections, etc.

## 📊 Available Metrics

### HTTP & Application Metrics
- `nextreel_http_requests_total` - Total HTTP requests by endpoint/status
- `nextreel_http_request_duration_seconds` - Request duration histograms  
- `nextreel_http_requests_in_progress` - Current requests being processed
- `nextreel_active_users` - Currently active users
- `nextreel_user_sessions_total` - Total user sessions created
- `nextreel_user_actions_total` - User actions by type

### Database Metrics  
- `nextreel_db_connections_active/idle/total` - Database pool status
- `nextreel_db_queries_total` - Database queries by type/table
- `nextreel_db_query_duration_seconds` - Query performance histograms
- `nextreel_db_circuit_breaker_state` - Circuit breaker status
- `nextreel_db_connection_errors_total` - Connection errors

### Movie Service Metrics
- `nextreel_movie_recommendations_total` - Recommendations served
- `nextreel_movie_queue_size` - User movie queue sizes
- `nextreel_movie_fetches_total` - Movie data fetches from sources
- `nextreel_tmdb_api_calls_total` - TMDB API usage
- `nextreel_tmdb_api_duration_seconds` - TMDB API performance

### Cache & Error Metrics
- `nextreel_cache_hits_total` / `nextreel_cache_misses_total` - Cache performance
- `nextreel_application_errors_total` - Application errors by type

## 🔔 Alerting (Optional)

Create alerts in Grafana for:

1. **High Error Rate**: `rate(nextreel_http_requests_total{status_code=~"5.."}[5m]) > 0.05`
2. **Database Issues**: `nextreel_db_circuit_breaker_state > 0`
3. **Slow Response Time**: `histogram_quantile(0.95, rate(nextreel_http_request_duration_seconds_bucket[5m])) > 2`
4. **Low Database Connections**: `nextreel_db_connections_idle < 2`

## 🔧 Troubleshooting

### Logs Not Appearing in Loki
- Check your `GRAFANA_LOKI_*` environment variables
- Verify credentials are correct
- Check application logs for Loki connection errors

### Metrics Not Collecting
- Ensure `/metrics` endpoint returns Prometheus format data
- Check that metrics collection started: look for "Metrics collection started" in logs
- Verify database pool metrics are being collected

### Dashboard Shows No Data
- Confirm Prometheus data source is configured correctly
- Check that your application is generating metrics (access some pages)
- Verify metric names match between dashboard and actual metrics

## 🎉 Success!

Once complete, you'll have:

- **Real-time visibility** into application performance
- **Structured, searchable logs** with correlation IDs  
- **Comprehensive metrics** covering HTTP, database, and business logic
- **Professional monitoring dashboard** 
- **Foundation for alerting** on critical issues

Your NextReel application now has enterprise-grade observability! 🚀

## 📚 Additional Resources

- [Grafana Cloud Documentation](https://grafana.com/docs/grafana-cloud/)
- [Prometheus Metrics Guide](https://prometheus.io/docs/practices/naming/)
- [Loki Log Aggregation](https://grafana.com/docs/loki/)