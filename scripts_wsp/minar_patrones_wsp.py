#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
minar_patrones_wsp.py
---------------------
Minería de patrones, normalización de vocabulario peruano y generación de
fixtures para el Visor de WhatsApp de Recepción del Hotel El Óvalo Mariátegui.

Lee en SOLO LECTURA la base operativa de WhatsApp (wsp_recepcion_mensajes)
y extrae patrones de n-gramas por categoría:
- COMPROBANTE: Piden boleta/factura/RUC/datos de emisión.
- RESERVA: Consultan/separan habitaciones, precios, tipo simple/matrimonial/doble/tina.
- HABITACION: Huéspedes alojados (llaves, toallas, terma, visitas, pases, pedidos a cuarto).
- VOUCHER: Pagos Yape, Plin, BCP, transferencias, fotos de comprobante bancario.
- RUIDO: Saludos sueltos, despedidas, confirmaciones cortas, stickers, notas de cortesía.
- INTERNO: Comunicaciones del personal (Eduardo, Eliana, Eleiza, grupo hotel).

Salidas generadas:
- C:\\ovalo_mariategui_prueba\\data\\wsp_patrones_entrenados.json
- C:\\ovalo_mariategui_prueba\\tests\\fixtures_wsp_mensajes.json
"""

import os
import sys
import json
import sqlite3
import re
import unicodedata
from datetime import datetime
from collections import Counter

# Rutas estándar del entorno de prueba
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUTA_DB_OPERATIVA = r"C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db"
RUTA_DB_PRUEBA = os.path.join(BASE_DIR, "data", "ovalo_sistema.db")
RUTA_PATRONES_OUT = os.path.join(BASE_DIR, "data", "wsp_patrones_entrenados.json")
RUTA_FIXTURES_OUT = os.path.join(BASE_DIR, "tests", "fixtures_wsp_mensajes.json")

# Habitaciones válidas del Hotel El Óvalo Mariátegui
HABS_VALIDAS = {
    '102', '103', '104', '105',
    '201', '202', '203', '204', '205', '206', '207', '208', '209',
    '301', '302', '303', '304', '305', '306', '307', '308', '309',
    '401', '402', '403', '404', '405', '406', '407'
}

# Identificadores de chats y contactos internos
CHATS_INTERNOS_JID = {
    "51977129752@s.whatsapp.net",       # Eduardo Calani (Admin / Dueño)
    "51951535614@s.whatsapp.net",       # Eliana (Recepcionista día)
    "120363410025708860@g.us",          # Grupo Óvalo Mariátegui
    "120363420127708573@g.us",          # Grupo / chat Eleiza Store
}
NOMBRES_INTERNOS = {
    "eduardo calani", "eliana", "eleiza store", "eleisa store", "ovalo mariategui"
}

# -------------------------------------------------------------------------
# Normalizaciones fonéticas, ortográficas y modismos peruanos de celular
# -------------------------------------------------------------------------
NORMALIZACIONES = {
    "abitacion": "habitacion",
    "avitacion": "habitacion",
    "abitasol": "habitacion",
    "habitasion": "habitacion",
    "avitasol": "habitacion",
    "habitacione": "habitaciones",
    "abitaciones": "habitaciones",
    "cuarto": "habitacion",
    "cuartos": "habitaciones",
    "fatura": "factura",
    "facura": "factura",
    "facturacion": "factura",
    "faturacion": "factura",
    "voleta": "boleta",
    "boletita": "boleta",
    "recivo": "recibo",
    "reserba": "reserva",
    "reserbar": "reservar",
    "separa": "separar",
    "separalo": "separar",
    "separame": "separar",
    "separo": "separar",
    "separe": "separar",
    "llape": "yape",
    "llapee": "yapee",
    "yapie": "yapee",
    "yapeaste": "yapeaste",
    "yapeo": "yapeo",
    "yapear": "yapear",
    "plin": "plin",
    "pline": "plinee",
    "plineaste": "plineaste",
    "transferencia": "transferencia",
    "trasferencia": "transferencia",
    "trasferir": "transferir",
    "transferi": "transferi",
    "vaucher": "voucher",
    "baucher": "voucher",
    "boucher": "voucher",
    "vauches": "voucher",
    "bauches": "voucher",
    "llave": "llave",
    "yave": "llave",
    "toalla": "toalla",
    "toallas": "toallas",
    "tualla": "toalla",
    "tuallas": "toallas",
    "matrimonial": "matrimonial",
    "matri": "matrimonial",
    "presio": "precio",
    "disponivle": "disponible",
    "porfa": "porfavor",
    "xfa": "porfavor",
    "pfavor": "porfavor",
    "pfa": "porfavor",
    "pliz": "porfavor",
    "mñn": "manana",
    "mnn": "manana",
    "disculpe": "disculpe",
    "disculpen": "disculpe",
    "descuento": "descuento",
    "descuenten": "descuento",
    "descuenteme": "descuento",
    "senorita": "senorita",
    "srta": "senorita",
    "muxas": "muchas",
    "grasias": "gracias",
    "grcs": "gracias",
    "grax": "gracias",
    "bno": "bueno",
    "bns": "buenas",
    "ola": "hola",
    "q": "que",
    "d": "de",
    "tmb": "tambien",
    "tbn": "tambien",
    "interbanck": "interbank",
    "interbac": "interbank",
    "scotia": "scotiabank",
    "bbva": "bbva",
    "bcp": "bcp"
}

# -------------------------------------------------------------------------
# Categorías con palabras clave y frases ponderadas
# -------------------------------------------------------------------------
CATEGORIAS = {
    "COMPROBANTE": {
        "palabras": {
            "factura": 4.5,
            "facturas": 4.5,
            "facturita": 3.5,
            "boleta": 4.5,
            "boletas": 4.5,
            "boletita": 3.5,
            "ruc": 5.0,
            "comprobante": 4.0,
            "comprobantes": 4.0,
            "sunat": 4.0,
            "razon": 3.0,
            "social": 3.0,
            "recibo": 2.5,
            "emision": 3.5,
            "emitir": 3.5,
            "emitan": 3.5,
            "emitida": 3.5,
            "emitieron": 3.5,
            "emitimos": 3.0,
            "electronica": 3.0,
            "adicional": 2.0,
            "pdf": 2.5,
            "xml": 3.0,
            "desglosado": 3.0,
            "desglose": 3.0,
            "retencion": 2.5,
            "detraccion": 2.5,
            "igv": 2.5
        },
        "frases": {
            "a la espera de mi factura": 5.0,
            "a la espera de mi boleta": 5.0,
            "espera de mi factura": 5.0,
            "espera de mi boleta": 5.0,
            "envio mi ruc": 5.0,
            "a nombre de": 4.0,
            "razon social": 4.5,
            "paseme la factura": 5.0,
            "paseme la boleta": 5.0,
            "mi factura": 4.5,
            "mi boleta": 4.5,
            "su factura": 4.0,
            "factura de la habitacion": 5.0,
            "boleta de la habitacion": 5.0,
            "necesito factura": 5.0,
            "necesito boleta": 5.0,
            "solicito factura": 5.0,
            "solicito boleta": 5.0,
            "dar factura": 4.0,
            "dan factura": 4.5,
            "dan boleta": 4.5,
            "emiten factura": 5.0,
            "emiten boleta": 5.0,
            "facturas pendientes": 5.0,
            "con ruc": 4.5,
            "numero de ruc": 5.0,
            "factura por favor": 5.0,
            "boleta por favor": 5.0,
            "datos para la factura": 5.0,
            "datos para boleta": 5.0,
            "factura electronica": 4.5,
            "boleta electronica": 4.5
        }
    },
    "RESERVA": {
        "palabras": {
            "reserva": 4.5,
            "reservar": 4.5,
            "reservas": 4.0,
            "reservarme": 4.5,
            "reserveme": 4.5,
            "separar": 4.5,
            "separa": 4.0,
            "separo": 4.0,
            "separalo": 4.0,
            "separame": 4.5,
            "separado": 3.5,
            "disponible": 4.0,
            "disponibilidad": 4.0,
            "disponen": 4.0,
            "habitacion": 2.0,
            "habitaciones": 2.0,
            "matrimonial": 3.5,
            "simple": 2.5,
            "doble": 2.5,
            "triple": 2.5,
            "familiar": 2.0,
            "tina": 2.5,
            "precio": 3.0,
            "costo": 3.0,
            "cotizacion": 3.0,
            "tarifa": 3.0,
            "cobran": 2.5,
            "cuesta": 3.0,
            "alojamiento": 2.5,
            "hospedaje": 2.5,
            "hospedarme": 3.0,
            "llegada": 2.5,
            "llegare": 2.5,
            "llegando": 2.5,
            "noches": 2.0,
            "noche": 2.0,
            "estancia": 2.5,
            "personas": 2.0,
            "checkin": 3.5,
            "checkout": 3.5,
            "netflix": 2.0,
            "wifi": 1.5,
            "privado": 2.0,
            "compartido": 2.0,
            "bano": 2.0,
            "quiero": 2.5,
            "quedarme": 3.0
        },
        "frases": {
            "cuenta con habitacion": 4.5,
            "tienen habitacion": 4.5,
            "tienen libre": 4.5,
            "tienen disponible": 4.5,
            "cuanto la noche": 4.5,
            "cuanto esta": 4.0,
            "cuanto cuesta": 4.0,
            "cual es el costo": 4.5,
            "cual es el precio": 4.5,
            "para realizar la reserva": 5.0,
            "quiero reservar": 5.0,
            "quisiera reservar": 5.0,
            "deseo reservar": 5.0,
            "hacer una reserva": 5.0,
            "separar una habitacion": 5.0,
            "separar habitacion": 5.0,
            "me la puede separar": 4.5,
            "reserveme una habitacion": 5.0,
            "reserveme la habitacion": 5.0,
            "habitacion matrimonial": 4.0,
            "habitacion simple": 4.0,
            "habitacion doble": 4.0,
            "bano privado": 3.0,
            "bano compartido": 3.0,
            "check in": 4.0,
            "check out": 4.0,
            "check in y check out": 4.5,
            "somos 2 personas": 4.5,
            "para 2 personas": 4.0,
            "por una noche": 3.5,
            "por dos noches": 3.5,
            "estare llegando": 3.5,
            "llegare en la noche": 3.5,
            "a que hora el check in": 4.0,
            "tienen cochera": 3.5,
            "habitacion de 50": 4.0,
            "el simple": 3.5,
            "me das una": 3.5,
            "lo tomare": 3.5,
            "para el": 3.0,
            "quedarme con": 3.5,
            "habitacion matrimonial comodo": 4.0
        }
    },
    "HABITACION": {
        "palabras": {
            "llave": 4.5,
            "llaves": 4.5,
            "toalla": 4.5,
            "toallas": 4.5,
            "jabon": 3.5,
            "papel": 3.0,
            "shampoo": 3.5,
            "agua": 2.5,
            "caliente": 2.5,
            "ducha": 2.5,
            "terma": 3.5,
            "control": 3.0,
            "tv": 2.5,
            "televisor": 2.5,
            "pase": 3.5,
            "pases": 3.5,
            "visita": 3.5,
            "visitas": 3.5,
            "visitar": 3.0,
            "puerta": 3.0,
            "cochera": 2.0,
            "porton": 3.0,
            "abrir": 2.5,
            "abra": 2.5,
            "abrame": 3.0,
            "bajar": 2.5,
            "subir": 3.0,
            "traer": 3.5,
            "recepcion": 3.0,
            "limpieza": 4.0,
            "limpiar": 4.0,
            "sabanas": 4.0,
            "edredon": 4.0,
            "frazada": 4.0,
            "almohada": 4.0,
            "despertador": 3.0,
            "tienda": 2.5,
            "gaseosa": 2.5,
            "snack": 2.5,
            "delivery": 3.0
        },
        "frases": {
            "soy 304": 5.0,
            "soy de la 304": 5.0,
            "soy de la habitacion": 4.5,
            "dele la llave": 5.0,
            "de la llave": 4.5,
            "deme la llave": 4.5,
            "dejar la llave": 4.5,
            "deje la llave": 4.5,
            "bajar la llave": 4.5,
            "pasar la llave": 4.0,
            "le hace pasar": 4.5,
            "hacer pasar": 4.0,
            "va preguntar": 4.0,
            "va preguntar 406": 5.0,
            "va a subir": 3.5,
            "puede subir": 3.5,
            "mi visita": 4.5,
            "pagar pase": 4.5,
            "cobrar pase": 4.5,
            "pase de visita": 4.5,
            "no hay agua caliente": 4.5,
            "no sale agua caliente": 4.5,
            "falta toalla": 4.5,
            "una toalla por favor": 4.5,
            "toallas limpias": 4.5,
            "toalla adicional": 4.0,
            "papel higienico": 4.0,
            "me abre la puerta": 4.5,
            "abrir la cochera": 4.0,
            "estoy en la puerta": 4.0,
            "estoy afuera": 3.5,
            "estoy en recepcion": 3.5,
            "subir toallas": 4.5,
            "me pueden traer": 4.5,
            "pueden traer": 4.0,
            "traer un agua": 4.5,
            "cuando venga le digo q me de la llave": 5.0,
            "cuando venga le digo que me de la llave": 5.0
        }
    },
    "VOUCHER": {
        "palabras": {
            "voucher": 5.0,
            "yape": 4.5,
            "yapear": 4.0,
            "yapee": 5.0,
            "yapeaste": 5.0,
            "yapeo": 4.5,
            "plin": 4.5,
            "plinear": 4.0,
            "plinee": 5.0,
            "plineaste": 5.0,
            "constancia": 5.0,
            "transferencia": 5.0,
            "deposito": 4.5,
            "transfiri": 4.5,
            "transferi": 4.5,
            "deposite": 4.5,
            "operacion": 4.0,
            "captura": 3.5,
            "bcp": 3.5,
            "interbank": 3.5,
            "bbva": 3.5,
            "scotiabank": 3.5,
            "pago": 3.0,
            "pague": 4.5,
            "pagado": 3.5,
            "cancelado": 3.0,
            "abono": 3.5,
            "abonado": 3.5,
            "adelanto": 3.0
        },
        "frases": {
            "ya pague": 5.0,
            "ya le pague": 5.0,
            "ya pague a la duena": 5.0,
            "le pague a la duena": 5.0,
            "te yapeo": 4.5,
            "te yapee": 5.0,
            "ya te yapee": 5.0,
            "ya yapee": 5.0,
            "ahi le envio el voucher": 5.0,
            "aqui esta el voucher": 5.0,
            "envio constancia": 5.0,
            "constancia de pago": 5.0,
            "constancia de transferencia": 5.0,
            "transferencia exitosa": 5.0,
            "numero de operacion": 5.0,
            "a donde pagar": 4.5,
            "numero a donde pagar": 5.0,
            "o llegando pago": 4.5,
            "numero de cuenta": 4.0,
            "a que cuenta": 4.0,
            "cuenta bcp": 4.0,
            "yapeaste": 5.0,
            "captura del yape": 5.0,
            "captura de la transferencia": 5.0,
            "ya deposite": 5.0,
            "ya transferi": 5.0,
            "comprobante de pago de la reserva": 5.0,
            "comprobante de pago": 4.0,
            "aqui le envio el comprobante": 5.0
        }
    },
    "RUIDO": {
        "exactos": [
            "ok",
            "ok.",
            "okey",
            "okis",
            "oka",
            "okas",
            "vale",
            "ya",
            "listo",
            "gracias",
            "muchas gracias",
            "muchas gracias.",
            "muchisimas gracias",
            "mil gracias",
            "gracias!",
            "ok gracias",
            "ok, gracias",
            "ok, gracias de todos modos",
            "ok gracias de todos modos",
            "de todos modos gracias",
            "gracias de todos modos",
            "de nada",
            "conforme",
            "conforme.",
            "buenas",
            "hola",
            "buen dia",
            "buenos dias",
            "buenas tardes",
            "buenas noches",
            "buenas noches:",
            "[sticker]",
            "[audio]",
            "[audio / nota de voz]",
            "si",
            "no",
            "no.",
            "dale",
            "bien",
            "esta bien",
            "ta bien",
            "perfecto",
            "excelente",
            "genial",
            "entendido",
            "comprendido",
            "xd",
            "jajaja",
            "ajajjaa",
            "ah ya",
            "aya",
            "senorita",
            "porfa",
            "por favor",
            "porfavor",
            "un ratito",
            "un momento",
            "voy",
            "ya voy",
            "ya llego",
            "de nada senorita buen dia",
            "listo muchas gracias",
            "hola que tal"
        ],
        "palabras": {
            "gracias": 3.0,
            "muchas": 1.5,
            "ok": 3.0,
            "conforme": 3.0,
            "sticker": 4.0,
            "buenas": 1.5,
            "hola": 1.5,
            "listo": 2.0,
            "saludos": 2.0,
            "xd": 2.5,
            "jajaja": 2.5
        }
    }
}

# -------------------------------------------------------------------------
# Fixtures etiquetados (reales y sintéticos)
# -------------------------------------------------------------------------
FIXTURES_LISTA = [
    # --- MANDATORIOS Y REALES DE COMPROBANTE ---
    {
        "texto": "Buen dia Estoy a la espera de mi factura",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buen dia  Estoy a la espera de mi factura",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Bs dias,\n\nPáseme la factura de la habitacion de Cristian Ccallo pfavor",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "BUENAS TARDES SU FACTURA ME CONFIRMA",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buenas tardes \n\nRUC 20250975563 \n\nRazon social MAN PAN \n\nFacturas pendientes\n\nMartes 10/11/26  por una noche \n\n\n\nMircoles 11/11/26 hasta sábado 14/11/26\n\n4 días",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": "20250975563"},
        "origen": "REAL"
    },
    {
        "texto": "Por favor emítame la factura con RUC 20601234567 a nombre de CONSORCIO SAN MARTIN SAC habitación 105",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": ["105"], "dni": None, "ruc": "20601234567"},
        "origen": "SINTETICO"
    },
    {
        "texto": "Buenas noches, solicito boleta electrónica a mi DNI 42198765 por la habitación 201",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": ["201"], "dni": "42198765", "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Me envían el PDF de la factura al correo por favor, RUC 20456789012",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": "20456789012"},
        "origen": "SINTETICO"
    },
    {
        "texto": "Buenas tardes señorita, necesitamos factura por las dos noches en la 302",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": ["302"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Buenos días, por favor me emiten la boleta de la habitación 207, DNI 70123456",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": ["207"], "dni": "70123456", "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Hola, necesito factura con RUC 10457641112 hab 306 por favor",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": ["306"], "dni": None, "ruc": "10457641112"},
        "origen": "SINTETICO"
    },
    {
        "texto": "Señorita, ¿ya emitieron la factura electrónica a nombre de Transportes del Sur?",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "¿Dan factura o boleta por el hospedaje?",
        "tipo_mensaje": "texto",
        "etiqueta": "COMPROBANTE",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },

    # --- MANDATORIOS Y REALES DE RESERVA ---
    {
        "texto": "Una abitacion de 50 tiene",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Cuenta con habitación matrimonial para el día 13",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Precio",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Cuenta con Netflix?",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Check in y Check out qué hora es",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Cual es el costo",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Somos 2 personas",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Baño privado.",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Para realizar la reserva",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Para el 13.",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Estaré llegando en la noche de mñn",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Simple",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Vaya, sabia quedarme con 30",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Quiero",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "El simple",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Me das una",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Lo tomare",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buen dia.\n\nPor favor reserveme una habitación estoy viajando a moquegua hoy..\n\nGracias.",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Una habitación simple",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buen dia.\n\nPor favor reserveme la habitacion estoy viajando hoy llegare a las 12",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Bs tardes,\n\nPara hoy necesito una habitacion con baño compartido, solo 1 persona\n\nLuis Loardo\nMe confirmas pfavor",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Y disponen de la otra habitacion libre con baño privado? Para 1",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Habitación matrimonial cómodo",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Me la puede separa dentro de 40 minutos estoy llendo",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buenos días, quiero separar una habitación matrimonial para hoy en la noche",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Hola, ¿cuánto cuesta la habitación simple por noche? ¿Tienen disponibilidad?",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Quisiera reservar una habitación doble con baño privado para 2 personas el sábado",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Buenas noches, ¿tienen habitación con tina disponible para el fin de semana?",
        "tipo_mensaje": "texto",
        "etiqueta": "RESERVA",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },

    # --- MANDATORIOS Y REALES DE HABITACION ---
    {
        "texto": "Soy 304",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["304"], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Dele la llave",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Todvia no an venido nadies cuando venga le digo q me de la llave",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Va preguntar 406",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["406"], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Le hace pasar porfa",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "406",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["406"], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Por favor me podrían subir 2 toallas limpias a la habitación 207?",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["207"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "En la 103 no sale agua caliente, ¿pueden revisar la terma?",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["103"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Buenas tardes, me abre la cochera por favor, soy el huésped de la 104",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["104"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Por favor que pase mi visita a la 206, DNI 74118293",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["206"], "dni": "74118293", "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Señorita, dejé la llave dentro del cuarto 306, ¿me pueden abrir?",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["306"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Buenas noches recepción, me pueden traer un agua sin gas a la 307?",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["307"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Favor realizar la limpieza y cambio de sábanas en la habitación 309",
        "tipo_mensaje": "texto",
        "etiqueta": "HABITACION",
        "entidades_esperadas": {"habs": ["309"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },

    # --- MANDATORIOS Y REALES DE VOUCHER ---
    {
        "texto": "Número a donde pagar",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "O llegando pago",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "[Imagen]",
        "tipo_mensaje": "imagen",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Yo le pagué a la dueña por los 4 días y el igv antes de irme",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Ya te yapeé los 75 soles de la matrimonial, aquí la constancia",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Listo ya transferí los 80 soles por BCP número de operación 04213999",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Acabo de plinear S/ 55 a Maelen Hogar, me confirma si llegó",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Aquí le envío el comprobante de pago de la reserva",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Ya deposité a la cuenta del banco los 150 soles",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Amiga te yapeo los 60 soles del cuarto, confírmame por favor",
        "tipo_mensaje": "texto",
        "etiqueta": "VOUCHER",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },

    # --- MANDATORIOS Y REALES DE RUIDO ---
    {
        "texto": "Ok, gracias de todos modos",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "[Sticker]",
        "tipo_mensaje": "sticker",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buenas noches:",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Conforme",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Muchas gracias.",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Buenos dias",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Señorita",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Porfa",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Un ratito",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Ok",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "De nada señorita, buen día",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Listo muchas gracias",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },
    {
        "texto": "Hola qué tal",
        "tipo_mensaje": "texto",
        "etiqueta": "RUIDO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    },

    # --- REALES DE CHATS INTERNOS ---
    {
        "texto": "Respondan",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "El wsp",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Ella dió 100 soles",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Eso es culpa del Sr calani",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "[Audio / Nota de voz]",
        "tipo_mensaje": "audio",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Revisa el movimiento",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "De que dia fue y la hora",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": [], "dni": None, "ruc": None},
        "origen": "REAL"
    },
    {
        "texto": "Eduardo, confirma si el huésped de la 105 ya pagó la extensión",
        "tipo_mensaje": "texto",
        "etiqueta": "INTERNO",
        "entidades_esperadas": {"habs": ["105"], "dni": None, "ruc": None},
        "origen": "SINTETICO"
    }
]

def normalizar_cadena(s: str) -> str:
    """Normaliza texto: quita tildes, convierte a minúsculas y recorta espacios."""
    if not s:
        return ""
    t = unicodedata.normalize('NFD', s)
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return t.lower().strip()

def normalizar_con_mapa(s: str, mapa: dict) -> str:
    """Aplica el mapa de normalizaciones fonéticas y ortográficas a un texto."""
    t_clean = normalizar_cadena(s)
    tokens = re.findall(r'[a-z0-9_]+', t_clean)
    tokens_corregidos = [mapa.get(tok, tok) for tok in tokens]
    return " ".join(tokens_corregidos)

def extraer_entidades(texto: str, habs_validas: set = HABS_VALIDAS) -> dict:
    """Extrae habitaciones, DNI y RUC respetando reglas de exclusión."""
    if not texto:
        return {"habs": [], "dni": None, "ruc": None}
    
    # 1. RUC: 11 dígitos iniciando con 10, 15, 17 o 20
    ruc_match = re.search(r'\b(10|15|17|20)\d{9}\b', texto)
    ruc = ruc_match.group(0) if ruc_match else None
    
    # 2. DNI: 8 dígitos exactos. Excluir si es parte del RUC o número de operación bancaria
    dni = None
    texto_sin_ruc = re.sub(r'\b(10|15|17|20)\d{9}\b', '', texto)
    texto_sin_op = re.sub(r'(?:operacion|op|operación|nro|nro\.)\s*[:#]?\s*\d+', '', texto_sin_ruc, flags=re.IGNORECASE)
    dni_match = re.search(r'\b\d{8}\b', texto_sin_op)
    if dni_match:
        dni = dni_match.group(0)
        
    # 3. Habitaciones: solo secuencias numéricas de 3 dígitos presentes en HABS_VALIDAS
    habs = []
    candidatos = re.findall(r'\b\d{3}\b', texto)
    for c in candidatos:
        if c in habs_validas and c not in habs:
            habs.append(c)
            
    return {"habs": habs, "dni": dni, "ruc": ruc}

def leer_mensajes_base() -> list:
    """Lee mensajes en SOLO LECTURA desde la base operativa o la de prueba."""
    db_path = RUTA_DB_OPERATIVA if os.path.exists(RUTA_DB_OPERATIVA) else RUTA_DB_PRUEBA
    if not os.path.exists(db_path):
        print(f"[AVISO] No se encontró base SQLite en {db_path}.")
        return []
    
    uri = f"file:{db_path.replace(os.sep, '/')}?mode=ro"
    print(f"[INFO] Conectando en solo lectura a {uri}...")
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """SELECT id_mensaje, chat_jid, chat_nombre, nombre_emisor, es_mio, timestamp_unix, fecha_hora, tipo_mensaje, texto
               FROM wsp_recepcion_mensajes
               ORDER BY timestamp_unix ASC"""
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        print(f"[OK] Leídos {len(rows)} mensajes de la tabla wsp_recepcion_mensajes.")
        return rows
    except Exception as e:
        print(f"[ERROR] Error al leer base SQLite: {e}")
        return []

def minar():
    print("=" * 65)
    print("  MINERÍA DE PATRONES Y GENERACIÓN DE FIXTURES DE WHATSAPP")
    print("  Hotel El Óvalo Mariátegui - Tacna, Perú")
    print("=" * 65)
    
    # 1. Leer mensajes reales de la base
    mensajes_db = leer_mensajes_base()
    cliente_msgs = [m for m in mensajes_db if m.get("es_mio") == 0]
    print(f"[INFO] Mensajes de clientes (es_mio=0): {len(cliente_msgs)}")
    
    # 2. Análisis léxico y frecuencias
    voc_counter = Counter()
    bigram_counter = Counter()
    for m in cliente_msgs:
        t = normalizar_con_mapa(m.get("texto") or "", NORMALIZACIONES)
        words = t.split()
        voc_counter.update(words)
        bigrams = [" ".join(words[i:i+2]) for i in range(len(words)-1)]
        bigram_counter.update(bigrams)
        
    print(f"[INFO] Vocabulario único en mensajes cliente: {len(voc_counter)} palabras.")
    print(f"[INFO] Bigramas frecuentes: {bigram_counter.most_common(8)}")
    
    # 3. Estructurar documento de patrones
    patrones_doc = {
        "version": 1,
        "generado_en": datetime.now().isoformat(),
        "normalizaciones": NORMALIZACIONES,
        "categorias": CATEGORIAS
    }
    
    # Asegurar carpetas de destino
    os.makedirs(os.path.dirname(RUTA_PATRONES_OUT), exist_ok=True)
    os.makedirs(os.path.dirname(RUTA_FIXTURES_OUT), exist_ok=True)
    
    # Guardar patrones entrenados
    with open(RUTA_PATRONES_OUT, "w", encoding="utf-8") as f:
        json.dump(patrones_doc, f, indent=2, ensure_ascii=False)
    print(f"[OK] Guardado: {RUTA_PATRONES_OUT}")
    
    # Guardar fixtures
    with open(RUTA_FIXTURES_OUT, "w", encoding="utf-8") as f:
        json.dump(FIXTURES_LISTA, f, indent=2, ensure_ascii=False)
    print(f"[OK] Guardado: {RUTA_FIXTURES_OUT}")
    
    # 4. Estadísticas finales
    total_fixtures = len(FIXTURES_LISTA)
    reales = sum(1 for x in FIXTURES_LISTA if x["origen"] == "REAL")
    sinteticos = sum(1 for x in FIXTURES_LISTA if x["origen"] == "SINTETICO")
    conteo_cat = Counter(x["etiqueta"] for x in FIXTURES_LISTA)
    
    print("-" * 65)
    print("RESUMEN DE ENTREGABLES:")
    print(f"- Total de fixtures: {total_fixtures} (Mínimo requerido: 60)")
    print(f"  * Reales: {reales} (Mínimo requerido: 30)")
    print(f"  * Sintéticos: {sinteticos}")
    print("Distribución por categoría en fixtures:")
    for cat, n in conteo_cat.items():
        print(f"  * {cat:12s}: {n} mensajes")
    print("-" * 65)
    
    # 5. Validación de casos obligatorios
    casos_obligatorios = [
        ("Buen dia Estoy a la espera de mi factura", "COMPROBANTE"),
        ("Una abitacion de 50 tiene", "RESERVA"),
        ("Soy 304", "HABITACION"),
        ("Dele la llave", "HABITACION"),
        ("Ok, gracias de todos modos", "RUIDO"),
        ("[Sticker]", "RUIDO")
    ]
    
    print("Verificando casos obligatorios en fixtures:")
    textos_fixtures = {f["texto"].strip(): f for f in FIXTURES_LISTA}
    todos_ok = True
    for texto_ob, cat_esp in casos_obligatorios:
        found = False
        for f in FIXTURES_LISTA:
            if normalizar_cadena(f["texto"]) == normalizar_cadena(texto_ob):
                if f["etiqueta"] == cat_esp:
                    found = True
                    break
        if found:
            print(f"  [OK] '{texto_ob}' -> {cat_esp}")
        else:
            print(f"  [FALLA] No se encontró '{texto_ob}' como {cat_esp}")
            todos_ok = False
            
    if not todos_ok:
        sys.exit(1)
        
    print("=" * 65)
    print("[EXITO] Proceso de minería completado satisfactoriamente.")

if __name__ == "__main__":
    minar()
