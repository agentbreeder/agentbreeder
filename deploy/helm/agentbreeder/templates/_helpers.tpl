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
{{- $pw := required "postgresql.auth.password must be set when postgresql.enabled (e.g. --set postgresql.auth.password=...), or disable it and set externalDatabaseUrl" .Values.postgresql.auth.password -}}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username $pw .Release.Name .Values.postgresql.auth.database -}}
{{- else -}}
{{- required "externalDatabaseUrl must be set when postgresql.enabled=false" .Values.externalDatabaseUrl -}}
{{- end -}}
{{- end -}}

{{- define "agentbreeder.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- $pw := required "redis.auth.password must be set when redis.enabled (e.g. --set redis.auth.password=...), or disable it and set externalRedisUrl" .Values.redis.auth.password -}}
{{- printf "redis://:%s@%s-redis-master:6379" $pw .Release.Name -}}
{{- else -}}
{{- required "externalRedisUrl must be set when redis.enabled=false" .Values.externalRedisUrl -}}
{{- end -}}
{{- end -}}

{{- define "agentbreeder.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" (include "agentbreeder.fullname" .)) -}}
{{- end -}}
