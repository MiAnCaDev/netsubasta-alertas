#!/bin/bash
#*********************************************************************************
# Nombre del shell script: subastas.sh
# Descripcion: lanza desde el cron el python de subastas para notificar
# Parametros: Ninguno.
#*********************************************************************************
PATH_LOG='/var/log/scriptsMiguel'
SERVICIO='subastas'
URL_CHECK="https://control-panel.legioagro.com/listaOpciones/switch/check_estado.php?servicio=${SERVICIO}"
CONFIG_FILE="/home/usuario/PROYECTOS/Subastas/scripts/subastas_telegram.cfg"
PYTHON_SCRIPT="/home/usuario/PROYECTOS/Subastas/alerta_subastas.py"

# PASO001: Generacion fichero .log
v_nombre=`basename $0 .sh`
v_fecha_batch=`date '+%y%m%d'`
exec 1>> ${PATH_LOG}/${v_nombre}_${v_fecha_batch}.log
exec 2>> ${PATH_LOG}/${v_nombre}_${v_fecha_batch}.log
echo "-------------------------------------------------------------"
echo "`date '+%T %D'` INFO: Inicio $v_nombre"

# PASO002: Cargar variables desde el fichero de configuración
if [ -f "$CONFIG_FILE" ]; then
    echo "`date '+%T %D'` INFO: Cargando configuración desde $CONFIG_FILE..."
    # Se utiliza source (o .) para importar TOKEN y CHAT_ID
    source "$CONFIG_FILE"
else
    echo "`date '+%T %D'` ERROR: No se encontró el fichero de configuración $CONFIG_FILE"
    echo "`date '+%T %D'` INFO: Fin $v_nombre"
    exit 1
fi

# Validar que TOKEN y CHAT_ID no estén vacíos
if [ -z "$TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "`date '+%T %D'` ERROR: TOKEN o CHAT_ID no están definidos en $CONFIG_FILE"
    echo "`date '+%T %D'` INFO: Fin $v_nombre"
    exit 1
fi

#PASO003: Leer el estado específico de n8n desde el JSON (por defecto 'true')
echo "`date '+%T %D'` INFO: Consultando servicio web $URL_CHECK..."
RESPUESTA_WEB=$(curl -s --max-time 10 "$URL_CHECK" | tr -d '[:space:]')
echo "`date '+%T %D'` INFO: Estado recibido: '$RESPUESTA_WEB'"
# Si la respuesta es explícitamente 'false', desactivamos.
# Si falla la conexión, da timeout, devuelve error o es 'true', forzamos 'true'.
if [[ "$RESPUESTA_WEB" == "false" ]]; then
	ESTADO_SERVICIO="false"
else
	ESTADO_SERVICIO="true"
fi
echo "`date '+%T %D'` INFO: Estado del servicio evaluado: '$ESTADO_SERVICIO'"

#PASO004: Comprobamos el estado
Notificamos si el servicio está activo
if [[ "$ESTADO_SERVICIO" == "true" ]]; then
	echo "`date '+%T %D'` INFO: Ejecutando script de subastas..."

	# 1. Ejecutamos Python y leemos la salida cambiando el delimitador a la línea de '='
	/usr/bin/python3 "$PYTHON_SCRIPT" | awk 'BEGIN{RS="========================================================================\n"} {print $0 "\0"}' | while IFS= read -r -d '' VEHICULO; do
		
		# Omitir bloques vacíos
		if [ -z "$(echo -e "$VEHICULO" | tr -d '[:space:]')" ]; then
			continue
		fi

		echo "`date '+%T %D'` INFO: Enviando vehículo a Telegram..."
		echo "$VEHICULO"

		# 2. Enviar por Telegram codificando la URL con --data-urlencode
		curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
			-d "chat_id=${CHAT_ID}" \
			-d "disable_web_page_preview=1" \
			--data-urlencode "text=${VEHICULO}" > /dev/null

		if [ $? -ne 0 ]; then 
			echo "`date '+%T %D'` ERROR: Fallo al notificar error en el curl"
		fi
		
		# Pausa opcional para evitar límite de rate limit de Telegram
		sleep 1
	done
else
	echo "`date '+%T %D'` INFO: Servicio inactivo (false o no disponible), se ignora la alerta"
fi

echo "`date '+%T %D'` INFO: Fin $v_nombre"
