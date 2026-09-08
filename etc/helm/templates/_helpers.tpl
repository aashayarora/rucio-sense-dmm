{{/* filepath: /home/users/aaarora/ci/dmm/helm/templates/_helpers.tpl */}}
{{/*
Expand the name of the chart.
*/}}
{{- define "dmm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "dmm.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "dmm.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "dmm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "dmm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dmm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
{{/*
Postgres runs in its own workload, so it needs a name that no other selector in
the chart matches. Selectors match on subsets, so giving the postgres pods the
DMM name plus an extra label would leave them selectable by the DMM Service -
ingress traffic would then get load-balanced onto port 80 of a database.
*/}}
{{- define "dmm.postgres.fullname" -}}
{{- printf "%s-postgres" (include "dmm.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dmm.postgres.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dmm.name" . }}-postgres
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "dmm.postgres.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "dmm.postgres.selectorLabels" . }}
app.kubernetes.io/component: database
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
