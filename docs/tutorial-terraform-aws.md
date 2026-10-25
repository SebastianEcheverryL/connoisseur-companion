# Tutorial: desplegar Connoisseur Companion en AWS con Terraform

Esta guía te lleva de una cuenta de AWS vacía a la app funcionando en internet: el backend en AWS Lambda y el frontend en Vercel. Cada paso explica qué crea, cuánto cuesta y cómo verificarlo en la consola. Al final está cómo borrar todo para no gastar créditos.

Los comandos están en **PowerShell** (la terminal de VS Code en Windows). Ejecútalos desde la raíz del repositorio salvo que el paso diga otra cosa.

## Índice

1. [Qué vas a construir](#1-qué-vas-a-construir)
2. [Cuánto cuesta](#2-cuánto-cuesta)
3. [Requisitos](#3-requisitos)
4. [Credenciales de AWS](#4-credenciales-de-aws)
5. [El bucket del estado de Terraform](#5-el-bucket-del-estado-de-terraform)
6. [Configurar el despliegue](#6-configurar-el-despliegue)
7. [Primer apply: el registro de imágenes](#7-primer-apply-el-registro-de-imágenes)
8. [Construir y subir la imagen](#8-construir-y-subir-la-imagen)
9. [Segundo apply: todo lo demás](#9-segundo-apply-todo-lo-demás)
10. [Cargar los secretos en SSM](#10-cargar-los-secretos-en-ssm)
11. [Probar el backend con una petición firmada](#11-probar-el-backend-con-una-petición-firmada)
12. [Verificar en la consola de AWS](#12-verificar-en-la-consola-de-aws)
13. [Conectar el frontend en Vercel](#13-conectar-el-frontend-en-vercel)
14. [Despliegues automáticos con GitHub Actions](#14-despliegues-automáticos-con-github-actions)
15. [Servidor MCP público (opcional)](#15-servidor-mcp-público-opcional)
16. [Logs, métricas y trazas](#16-logs-métricas-y-trazas)
17. [Errores comunes](#17-errores-comunes)
18. [Destruir todo](#18-destruir-todo)

---

## 1. Qué vas a construir

```
Navegador ──► Vercel (Next.js)
                │  /api/chat: revisa la sesión de GitHub y firma la petición (SigV4)
                │  con credenciales temporales que Vercel obtiene por OIDC: no hay llaves guardadas
                ▼
        Lambda Function URL  (autenticación IAM, respuesta en streaming)
                │
        Lambda "connoisseur-prod-api"  (imagen de contenedor desde ECR)
          FastAPI + LangGraph + índice ChromaDB + modelos ONNX dentro de la imagen
                ├─► Bedrock: Amazon Nova Lite (el LLM) y Bedrock Guardrails
                ├─► DynamoDB: cuotas por usuario (mensajes por minuto y por día)
                ├─► SSM Parameter Store: llaves de Langfuse, Tavily y el token MCP
                ├─► OpenStreetMap y Tavily (búsqueda web)
                └─► CloudWatch (logs, métricas, alarmas)  +  Langfuse Cloud (trazas de los agentes)
```

Las decisiones importantes:

- **Lambda en vez de un servidor.** Solo pagas cuando alguien usa la app. Sin tráfico el costo es casi cero.
- **Function URL con autenticación IAM.** Nadie en internet puede llamar al backend directamente. Solo el rol de Vercel tiene permiso.
- **Bedrock en vez de Ollama.** En Lambda no hay GPU. Nova Lite es barato y se cobra por token.
- **Secretos en SSM, nunca en Terraform.** Terraform crea los parámetros con un valor de relleno. Tú pones el valor real con un comando. Así el secreto nunca aparece en archivos ni en el estado de Terraform.

## 2. Cuánto cuesta

Precios de us-east-1 en octubre de 2026. Revisa la [página de precios](https://aws.amazon.com/pricing/) antes de desplegar.

| Recurso | Cómo se cobra | Estimado para una demo |
|---|---|---|
| Lambda | 400.000 GB-segundo gratis al mes, siempre. Luego ~$0,0000167 por GB-segundo | $0. Mil mensajes de ~40 s a 2 GB son ~80.000 GB-s |
| Bedrock Nova Lite | $0,06 por millón de tokens de entrada, $0,24 por millón de salida | ~$1,50 por cada mil mensajes |
| Bedrock Guardrails | Filtros de contenido $0,15 por cada 1.000 unidades de texto. Una unidad son 1.000 caracteres | ~$0,50 por cada mil mensajes |
| ECR | $0,10 por GB al mes de imágenes guardadas | ~$0,10 a $0,30 al mes |
| DynamoDB | Bajo demanda, por escritura | Centavos al mes |
| CloudWatch | Logs $0,50 por GB. Las primeras 10 alarmas y 3 dashboards son gratis | Centavos al mes |
| SSM Parameter Store | Nivel estándar | $0 |
| S3 (estado de Terraform) | Por GB guardado | Menos de $0,01 |
| AWS Budgets | Los dos primeros presupuestos son gratis | $0 |
| Vercel Hobby, Langfuse Cloud Hobby, Tavily free | Planes gratuitos | $0 |

**Total esperado: de $1 a $5 al mes** con uso de demo. Sin tráfico, unos centavos.

**Sobre los créditos.** Las cuentas nuevas desde julio de 2025 eligen entre un plan Free y un plan Paid. El plan Free no cobra nada, pero vence a los 6 meses o cuando se acaban los créditos, lo que pase primero. Revisa tu plan y la fecha de vencimiento en **Billing and Cost Management → Free Tier** y **Credits**.

## 3. Requisitos

Instala estas herramientas una sola vez:

```powershell
winget install Hashicorp.Terraform
winget install Amazon.AWSCLI
# Docker Desktop ya está instalado si seguiste las fases anteriores
```

Cierra y abre la terminal, y verifica:

```powershell
terraform version   # 1.10 o más reciente (usa el bloqueo nativo de S3)
aws --version       # 2.32 o más reciente para usar "aws login"
docker version
```

También necesitas:

- Una cuenta de AWS. No uses el usuario raíz para el día a día.
- Una cuenta de Vercel con el repositorio de GitHub conectado.
- Una cuenta de [Langfuse Cloud](https://cloud.langfuse.com) (plan Hobby gratis) para las trazas. Es opcional pero recomendado.
- Tu llave de [Tavily](https://tavily.com) si quieres calificaciones de reseñas. También opcional.

## 4. Credenciales de AWS

La forma más simple es `aws login`, que abre el navegador y usa tu sesión de la consola. Las credenciales duran unas horas y no se guardan llaves en disco:

```powershell
aws login
aws sts get-caller-identity
```

El segundo comando debe mostrar tu número de cuenta y tu usuario. Si muestra `root`, crea un usuario en **IAM Identity Center** con el permiso `AdministratorAccess`, activa MFA y vuelve a entrar con ese usuario.

> **Por qué no llaves de acceso.** Una llave `AKIA...` guardada en `~/.aws/credentials` no vence. Si se filtra, alguien puede gastar tus créditos. Las sesiones temporales vencen solas.

## 5. El bucket del estado de Terraform

Terraform guarda en un archivo de estado qué recursos creó. Lo guardamos en S3, con versiones, para no perderlo. Este bucket se crea una sola vez y con estado local:

```powershell
$account = aws sts get-caller-identity --query Account --output text
cd infra/terraform/bootstrap
terraform init
terraform apply -var="bucket_name=connoisseur-tfstate-$account"
cd ../../..
```

Escribe `yes` cuando lo pida. Esto crea:

- **Un bucket S3** privado, cifrado y con versiones. Cada `apply` guarda una versión nueva del estado.
- **Una regla de ciclo de vida** que borra las versiones viejas a los 90 días.

El bucket tiene `prevent_destroy`, así que un `terraform destroy` accidental no lo borra.

**Verifica:** en la consola, entra a **S3** y busca `connoisseur-tfstate-<tu-cuenta>`. En **Properties** debe decir *Bucket Versioning: Enabled*.

## 6. Configurar el despliegue

Copia los dos archivos de ejemplo. Ambos están en `.gitignore`:

```powershell
cd infra/terraform
Copy-Item backend.hcl.example backend.hcl
Copy-Item terraform.tfvars.example terraform.tfvars
```

En `backend.hcl` cambia el nombre del bucket por el que creaste en el paso 5.

En `terraform.tfvars` ajusta:

| Variable | Qué poner |
|---|---|
| `alarm_email` | Tu correo. Ahí llegan las alarmas y las alertas de presupuesto |
| `monthly_budget_usd` | El límite mensual para las alertas, por ejemplo 10 |
| `vercel_team_slug` | En Vercel: **Settings → General → Team URL**. Es la parte final de la URL |
| `vercel_project_name` | El nombre del proyecto en Vercel |
| `github_repository` | `TuUsuario/connoisseur-companion` |
| `create_github_oidc_provider` | `false` si tu cuenta ya tiene el proveedor OIDC de GitHub |

Luego inicializa Terraform con el bucket remoto:

```powershell
terraform init "-backend-config=backend.hcl"
```

> **Por qué las comillas.** PowerShell parte en dos un argumento sin comillas que empieza con `-` y contiene un punto: Terraform recibiría `-backend-config=backend` y `.hcl` por separado y fallaría con *No positional arguments are expected*. Lo mismo pasa con `-target=module.ecr` más abajo.

## 7. Primer apply: el registro de imágenes

La Lambda necesita que su imagen ya exista en ECR. Por eso primero creamos solo el repositorio:

```powershell
terraform apply "-target=module.ecr"
```

Crea **un repositorio ECR** llamado `connoisseur-prod`:

- Las etiquetas son inmutables. Una etiqueta nunca se sobreescribe con otro código.
- Escanea cada imagen al subirla, sin costo.
- Una regla guarda solo las últimas 10 imágenes para ahorrar almacenamiento.

Terraform avisa que `-target` es para casos excepcionales. Aquí es justo eso: romper el "huevo y la gallina" de la primera imagen.

## 8. Construir y subir la imagen

La imagen se construye en dos etapas. La primera descarga los datos de IBM, construye el índice vectorial y descarga los modelos ONNX. La segunda copia solo lo necesario. El resultado pesa ~1,5 GB y la Lambda nunca descarga modelos al arrancar.

```powershell
cd ../..   # raíz del repo
$account = aws sts get-caller-identity --query Account --output text
$registry = "$account.dkr.ecr.us-east-1.amazonaws.com"
docker login --username AWS --password (aws ecr get-login-password --region us-east-1) $registry

docker buildx build --platform linux/amd64 --provenance=false -t "$registry/connoisseur-prod:bootstrap" --push .
```

La primera vez tarda de 10 a 20 minutos.

- El login pasa el token como argumento. La forma habitual, `aws ecr get-login-password | docker login --password-stdin`, falla en Windows PowerShell 5.1 con `400 Bad Request`, porque el pipe cambia la codificación y agrega un salto de línea al token. En PowerShell 7 o en Git Bash el pipe sí funciona. Docker avisa que `--password` es inseguro, pero el token vence a las 12 horas.

- `--platform linux/amd64` es la arquitectura de la Lambda.
- `--provenance=false` es **obligatorio**. Sin esa opción Docker sube un índice con varias entradas y Lambda lo rechaza.
- La etiqueta `bootstrap` es la que espera Terraform la primera vez. Después, GitHub Actions usa el hash del commit.

**Verifica:** en **ECR → Repositories → connoisseur-prod** debe aparecer la imagen `bootstrap`. En la columna de vulnerabilidades verás el resultado del escaneo.

**Prueba local opcional.** Puedes correr la imagen como lo haría Lambda, con el disco en solo lectura:

```powershell
docker run --rm --read-only --tmpfs /tmp:rw,exec,size=1g -p 8010:8080 "$registry/connoisseur-prod:bootstrap"
# en otra terminal:
curl.exe http://localhost:8010/health
```

## 9. Segundo apply: todo lo demás

```powershell
cd infra/terraform
terraform plan
```

Lee el plan antes de aplicarlo. La última línea debe ser `Plan: N to add, 0 to change, 0 to destroy`: solo creaciones, nada que cambie o se borre. Estos son los grupos de recursos:

| Módulo | Qué crea | Costo |
|---|---|---|
| `dynamodb` | La tabla `connoisseur-prod-quotas`. Los contadores vencen solos con TTL | Centavos |
| `ssm` | 4 parámetros SecureString con el valor `CHANGE_ME` | $0 |
| `guardrail` | Un Bedrock Guardrail con filtros de odio, insultos, contenido sexual, violencia, conducta indebida, ataques de prompt, datos sensibles y groserías. También una versión numerada | Solo por uso |
| `observability` | El grupo de logs con 14 días de retención, métricas a partir de los logs JSON, 6 alarmas, el tópico SNS para el correo y un dashboard | Centavos |
| `iam` | El rol de la Lambda con permisos mínimos, el rol de Vercel y el rol de GitHub Actions. Los dos últimos usan OIDC | $0 |
| `api` | La función Lambda de 2 GB y 300 s, y su Function URL con autenticación IAM y streaming | Free tier |
| `budget` | Un presupuesto mensual con alertas al 50 % y al 80 % del gasto real, y al 100 % del pronóstico | $0 |

El presupuesto **no cuenta los créditos**. Si los contara, el gasto neto sería siempre $0 y nunca te avisaría.

Aplica:

```powershell
terraform apply
```

Al final Terraform imprime las salidas. Guárdalas:

```powershell
terraform output
```

| Salida | Para qué sirve |
|---|---|
| `api_url` | La URL del backend. Es `BACKEND_URL` en Vercel |
| `vercel_role_arn` | El rol que asume Vercel. Es `AWS_ROLE_ARN` en Vercel |
| `github_deploy_role_arn` | El rol de los despliegues automáticos |
| `ssm_parameter_path` | Dónde van los secretos |
| `dashboard_url` | El dashboard de CloudWatch |

**Confirma el correo.** AWS envía un correo de *AWS Notifications* para confirmar la suscripción a las alarmas. Sin confirmarlo, las alarmas no te llegan.

## 10. Cargar los secretos en SSM

La app lee todos los parámetros de `/connoisseur/prod/` al arrancar e ignora los que siguen en `CHANGE_ME`. Pon solo los que uses:

```powershell
$path = "/connoisseur/prod"
aws ssm put-parameter --name "$path/LANGFUSE_PUBLIC_KEY" --type SecureString --overwrite --value "pk-lf-..."
aws ssm put-parameter --name "$path/LANGFUSE_SECRET_KEY" --type SecureString --overwrite --value "sk-lf-..."
aws ssm put-parameter --name "$path/TAVILY_API_KEY" --type SecureString --overwrite --value "tvly-..."
```

El token MCP solo hace falta si activas el servidor MCP público del paso 15. Puedes generar uno aleatorio así:

```powershell
$token = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
aws ssm put-parameter --name "$path/MCP_TOKEN" --type SecureString --overwrite --value $token
```

Las llaves de Langfuse están en Langfuse Cloud, en **Settings → API Keys** de tu proyecto. Si tu proyecto está en la región de Estados Unidos, cambia `langfuse_host` a `https://us.cloud.langfuse.com` en `terraform.tfvars` y vuelve a aplicar.

**Los secretos se leen solo al arrancar.** Para que una Lambda ya encendida los tome, fuerza un arranque nuevo:

```powershell
aws lambda update-function-configuration --function-name connoisseur-prod-api --description "secrets $(Get-Date -Format s)"
```

## 11. Probar el backend con una petición firmada

La Function URL rechaza peticiones sin firma. Compruébalo:

```powershell
$url = terraform output -raw api_url
curl.exe -s "${url}health"
# {"Message":"Forbidden"}
```

Ahora firma la petición con tus credenciales temporales. `curl` firma con SigV4 por sí solo:

```powershell
aws configure export-credentials --format powershell | Invoke-Expression
curl.exe -s --aws-sigv4 "aws:amz:us-east-1:lambda" `
  --user "${env:AWS_ACCESS_KEY_ID}:${env:AWS_SECRET_ACCESS_KEY}" `
  -H "x-amz-security-token: $env:AWS_SESSION_TOKEN" "${url}health"
```

La primera respuesta tarda de 10 a 30 segundos: es el arranque en frío, que copia el índice a `/tmp` y carga los modelos. Debe responder:

```json
{"status":"ok","version":"0.2.0","llm_provider":"bedrock","vector_index":true,"tracing":true}
```

`tracing: true` confirma que las llaves de Langfuse se leyeron de SSM. Ahora una pregunta real:

```powershell
'{"message":"Find me a moody Italian spot in Pasadena"}' | Set-Content -NoNewline body.json
curl.exe -s --aws-sigv4 "aws:amz:us-east-1:lambda" `
  --user "${env:AWS_ACCESS_KEY_ID}:${env:AWS_SECRET_ACCESS_KEY}" `
  -H "x-amz-security-token: $env:AWS_SESSION_TOKEN" -H "content-type: application/json" `
  --data-binary "@body.json" "${url}chat/sync"
Remove-Item body.json
```

## 12. Verificar en la consola de AWS

| Dónde | Qué deberías ver |
|---|---|
| **Lambda → connoisseur-prod-api** | En *Configuration → Function URL*: auth type `AWS_IAM` e invoke mode `RESPONSE_STREAM`. En *Environment variables*: ninguna llave, solo nombres y rutas |
| **Lambda → Monitor** | Las invocaciones de tus pruebas |
| **CloudWatch → Log groups → /aws/lambda/connoisseur-prod-api** | Una línea JSON por evento. Busca `ssm_parameters_loaded` para ver qué secretos se cargaron. Muestra los nombres, nunca los valores |
| **DynamoDB → Tables → connoisseur-prod-quotas → Explore items** | Contadores `rpm:` y `day:` por usuario |
| **Bedrock → Guardrails → connoisseur-prod** | La versión 1 y sus filtros. En *Test* puedes probar mensajes |
| **CloudWatch → Dashboards → connoisseur-prod** | Invocaciones, latencia, tokens de Bedrock y señales de abuso |
| **CloudWatch → Alarms** | 6 alarmas en estado `OK` o `Insufficient data` |
| **Billing → Budgets** | `connoisseur-prod-monthly` |
| **Langfuse Cloud → Traces** | Una traza por pregunta, con cada agente y cada llamada a Bedrock |

## 13. Conectar el frontend en Vercel

1. **Activa OIDC.** En Vercel, entra a **Settings → Security → Secure backend access with OIDC federation**, actívalo y elige el modo **Team**. Así el emisor es `https://oidc.vercel.com/<tu-equipo>`, que es lo que espera el rol de Terraform.
2. **Importa el proyecto.** Usa el mismo nombre que pusiste en `vercel_project_name` y pon `web` como **Root Directory**.
3. **Crea una OAuth App de GitHub** en **GitHub → Settings → Developer settings → OAuth Apps**. La *Authorization callback URL* es `https://<tu-dominio>.vercel.app/api/auth/callback/github`.
4. **Variables de entorno** del proyecto, en el entorno *Production*:

| Variable | Valor |
|---|---|
| `BACKEND_URL` | `terraform output -raw api_url`, sin la barra final |
| `BACKEND_AUTH` | `aws_iam` |
| `AWS_ROLE_ARN` | `terraform output -raw vercel_role_arn` |
| `AWS_REGION` | `us-east-1` |
| `AUTH_SECRET` | El resultado de `npx auth secret` |
| `AUTH_GITHUB_ID` y `AUTH_GITHUB_SECRET` | Los de la OAuth App |

5. **Despliega** y entra con GitHub. La primera respuesta puede tardar por el arranque en frío.

El rol solo confía en el entorno `production` de ese proyecto. Las *Preview deployments* no pueden llamar al backend, a propósito.

## 14. Despliegues automáticos con GitHub Actions

El flujo `.github/workflows/deploy.yml` corre en cada push a `main` que cambie el código del backend:

1. Construye la imagen.
2. La escanea con Trivy y falla si hay vulnerabilidades críticas o altas con arreglo disponible.
3. La sube a ECR con el hash del commit como etiqueta.
4. Actualiza la Lambda.
5. Llama a `/health` con una petición firmada.

No usa llaves de AWS. GitHub obtiene un token OIDC y asume el rol de Terraform, que solo acepta la rama `main` de tu repositorio.

Para activarlo, en GitHub entra a **Settings → Secrets and variables → Actions → Variables** y crea:

| Variable | Valor |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw github_deploy_role_arn` |
| `AWS_REGION` | `us-east-1` |
| `ECR_REPOSITORY` | `connoisseur-prod` |
| `LAMBDA_FUNCTIONS` | `connoisseur-prod-api`. Si activas el MCP público: `connoisseur-prod-api connoisseur-prod-mcp` |

Mientras `AWS_DEPLOY_ROLE_ARN` no exista, el flujo se salta sin fallar.

**Quién cambia qué.** GitHub Actions actualiza el código de la Lambda. Terraform cambia la infraestructura y tú lo aplicas a mano. Por eso la Lambda tiene `ignore_changes = [image_uri]`: un `terraform apply` nunca devuelve la función a una imagen vieja.

## 15. Servidor MCP público (opcional)

Los clientes MCP, como MCP Inspector o Claude Desktop, no saben firmar con SigV4. Para exponer las herramientas MCP, Terraform puede crear una segunda Lambda con la misma imagen y una URL pública. En esa función, `/chat` responde 404 y `/mcp` exige el token `MCP_TOKEN`.

1. Carga `MCP_TOKEN` en SSM como en el paso 10.
2. Pon `enable_public_mcp = true` en `terraform.tfvars` y aplica.
3. Conecta el cliente a `terraform output -raw mcp_url` seguido de `mcp/`, con el encabezado `Authorization: Bearer <tu token>`.

## 16. Logs, métricas y trazas

En **CloudWatch → Logs Insights**, elige el grupo de la API y prueba estas consultas:

```
# Preguntas más lentas
fields @timestamp, ms, path | filter event = "http_request" | sort ms desc | limit 20

# Mensajes bloqueados por los guardrails y por qué
fields @timestamp, stage, category | filter event = "guardrail_blocked" | sort @timestamp desc

# Usuarios que chocan con la cuota
filter event = "quota_exceeded" | stats count() by user
```

Las alarmas envían correo cuando:

- La función falla 3 veces en 5 minutos.
- Hay un rechazo por límite de concurrencia.
- Una respuesta se acerca al timeout.
- El flujo de agentes falla 3 veces.
- Hay más de 20 rechazos por cuota en 5 minutos, que puede ser un intento de gastar tus créditos.
- Hay más de 10 bloqueos de guardrails en 5 minutos.

## 17. Errores comunes

| Error | Causa | Solución |
|---|---|---|
| `The image manifest, config or layer media type for the source image ... is not supported` | La imagen se subió con *provenance* y quedó como índice multi-plataforma | Reconstruye con `--provenance=false` y una etiqueta nueva |
| `Specified ReservedConcurrentExecutions ... decreases account's UnreservedConcurrentExecution below its minimum value of [10]` | Las cuentas nuevas tienen un límite de 10 ejecuciones y no permiten reservar ninguna | Deja `reserved_concurrency = -1`. Pide un aumento en **Service Quotas → AWS Lambda → Concurrent executions** si quieres reservar |
| `{"Message":"Forbidden"}` al llamar la URL | La petición no está firmada, se firmó para otro servicio o región, o el rol no tiene permiso | Firma con el servicio `lambda` y la región correcta. El rol necesita `lambda:InvokeFunctionUrl` y `lambda:InvokeFunction`. Terraform ya da ambos |
| `AccessDeniedException` de Bedrock en los logs | El modelo o el perfil de inferencia no coinciden con la política IAM | `bedrock_model_id` y `bedrock_foundation_model` deben corresponder. Nova se habilita solo al primer uso. Los modelos de Anthropic piden un formulario aparte |
| `EntityAlreadyExists: Provider with url https://token.actions.githubusercontent.com already exists` | Tu cuenta ya tenía el proveedor OIDC de GitHub | `create_github_oidc_provider = false` y aplica de nuevo |
| En Vercel: `Not authorized to perform sts:AssumeRoleWithWebIdentity` | El *slug* del equipo, el nombre del proyecto o el entorno no coinciden con el rol | Revisa `vercel_team_slug` y `vercel_project_name`, y que OIDC esté en modo **Team**. El rol solo acepta `production` |
| `ImageTagAlreadyExistsException` | Las etiquetas son inmutables | Usa una etiqueta nueva, por ejemplo el hash del commit |
| `Error acquiring the state lock` | Otro `terraform apply` quedó a medias | Si estás seguro de que no corre otro: `terraform force-unlock <ID>` |
| `/health` responde `"tracing": false` | Las llaves de Langfuse siguen en `CHANGE_ME`, o la Lambda arrancó antes de cargarlas | Revisa el paso 10 y fuerza un arranque nuevo |
| La primera pregunta da timeout en Vercel | El arranque en frío más el tiempo de los agentes | Reintenta. Las siguientes son más rápidas. Las respuestas ya llegan por streaming |
| No llegan las alertas | La suscripción SNS no se confirmó | Busca el correo de *AWS Notifications*, también en spam |

## 18. Destruir todo

Cuando ya no necesites la demo, borra todo para no gastar créditos:

```powershell
cd infra/terraform
terraform destroy
```

Esto borra la Lambda, la URL, las imágenes de ECR, la tabla, los parámetros de SSM, el guardrail, los logs, las alarmas, el presupuesto y los roles. Revisa el plan antes de escribir `yes`.

El bucket del estado sobrevive porque tiene `prevent_destroy`. Si también quieres borrarlo:

1. En `infra/terraform/bootstrap/main.tf`, quita el bloque `lifecycle { prevent_destroy = true }`.
2. Vacía el bucket en la consola de S3. Hay que vaciar también las versiones: botón **Empty**.
3. Ejecuta `terraform destroy -var="bucket_name=connoisseur-tfstate-<tu-cuenta>"` en `infra/terraform/bootstrap`.

Fuera de AWS:

- Borra el proyecto en Vercel y la OAuth App en GitHub.
- Borra las variables del repositorio para que el flujo de despliegue deje de correr.
- Si quieres, borra el proyecto en Langfuse Cloud.

Revisa **Billing → Cost Explorer** 48 horas después. El gasto diario debe bajar a cero.
