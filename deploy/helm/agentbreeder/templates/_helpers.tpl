{{- define "agentbreeder.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentbreeder.apiImage" -}}
{{- printf "%s/%s/%s:%s" .Values.image.registry .Values.image.repository .Values.image.apiImage .Values.image.tag -}}
{{- end -}}

{{- define "agentbreeder.dashboardImage" -}}
{{- printf "%s/%s/%s:%s" .Values.image.registry .Values.image.repository .Values.image.dashboardImage .Values.image.tag -}}
{{- end -}}

{{- define "agentbreeder.databaseUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password .Release.Name .Values.postgresql.auth.database -}}
{{- else -}}
{{- .Values.externalDatabaseUrl -}}
{{- end -}}
{{- end -}}

{{- define "agentbreeder.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s-redis-master:6379" .Release.Name -}}
{{- else -}}
{{- .Values.externalRedisUrl -}}
{{- end -}}
{{- end -}}

{{- define "agentbreeder.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" (include "agentbreeder.fullname" .)) -}}
{{- end -}}
