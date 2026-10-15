# Diccionario de datos: e-commerce Olist

## Contexto general del dataset

Olist es un marketplace brasileño que conecta pequeños vendedores con grandes tiendas online. El dataset contiene unas 99.000 órdenes realizadas entre septiembre de 2016 y octubre de 2018. Todos los montos están en reales brasileños (BRL). La fecha de referencia de una orden es siempre `orders.order_purchase_timestamp` (fecha de compra).

## Período con datos completos y "último trimestre"

Los datos completos van de enero de 2017 a agosto de 2018. Los meses de 2016 y los de septiembre y octubre de 2018 tienen muy pocas órdenes y deben excluirse de comparaciones entre períodos.
- "Último mes" significa agosto de 2018.
- "Último trimestre" significa el último trimestre completo: Q2 2018 (abril a junio de 2018). Si se pide comparar con el trimestre anterior, es Q1 2018 (enero a marzo de 2018).
- "Último año completo" significa 2017.
- Si se pregunta por "este año" o el año actual, usar 2018 hasta agosto inclusive.

## Tabla orders

Una fila por orden. Columnas clave:
- `order_id`: identificador de la orden.
- `customer_id`: enlaza con `customers.customer_id`. Ojo: es un id por orden, no por persona.
- `order_status`: estado de la orden (ver sección "Estados de orden").
- `order_purchase_timestamp`: fecha y hora de compra. Es la fecha que se usa para agrupar ventas por día, mes, trimestre o año.
- `order_approved_at`: fecha de aprobación del pago.
- `order_delivered_carrier_date`: fecha en que se entregó al transportista.
- `order_delivered_customer_date`: fecha en que el cliente recibió la orden.
- `order_estimated_delivery_date`: fecha de entrega prometida al cliente.

## Estados de orden y qué cuenta como venta

`orders.order_status` puede ser: delivered, shipped, invoiced, processing, approved, created, canceled, unavailable.
- Una orden cuenta como venta si su estado NO es `canceled` ni `unavailable`.
- Para métricas de entrega (tiempos, atrasos) usar solo órdenes con estado `delivered` y `order_delivered_customer_date` no nulo.
- Cerca del 97% de las órdenes están en estado `delivered`.

## Tabla order_items

Una fila por ítem dentro de una orden. Una orden puede tener varios ítems.
- `order_item_id`: número de ítem dentro de la orden (1, 2, 3...).
- `product_id`: enlaza con `products`.
- `seller_id`: enlaza con `sellers`. Cada ítem tiene su propio vendedor.
- `price`: precio del ítem, sin flete.
- `freight_value`: costo de envío asignado a ese ítem.
- Cantidad de unidades vendidas = cantidad de filas en `order_items`.

## Tabla order_payments

Una o más filas por orden: un cliente puede pagar una orden combinando medios de pago.
- `payment_sequential`: número de pago dentro de la orden.
- `payment_type`: credit_card, boleto (pago en efectivo con comprobante bancario), voucher, debit_card, not_defined.
- `payment_installments`: cantidad de cuotas.
- `payment_value`: monto de ese pago, incluye flete.
- Advertencia: nunca hacer JOIN entre `order_items` y `order_payments` en la misma consulta para sumar montos, porque se duplican filas. Si se necesitan ambos, agregar cada tabla por `order_id` en una subconsulta antes de unir.

## Tabla customers

- `customer_id`: id del cliente para UNA orden. Cada orden tiene un `customer_id` distinto.
- `customer_unique_id`: identifica a la persona real. Para contar clientes únicos, clientes recurrentes o compras por cliente usar SIEMPRE `customer_unique_id`, nunca `customer_id`.
- `customer_city`, `customer_state`: ciudad y estado (sigla de 2 letras, por ejemplo SP o RJ) donde vive el cliente.

## Tabla sellers

- `seller_id`: identificador del vendedor.
- `seller_city`, `seller_state`: ubicación del vendedor.
- Para ventas por vendedor, unir `order_items` con `sellers` por `seller_id`.

## Tabla products y category_translation

- `products.product_category_name`: categoría en portugués. Unas 600 filas tienen categoría nula.
- `category_translation` traduce la categoría al inglés (`product_category_name_english`).
- Al mostrar categorías, usar el nombre en inglés con LEFT JOIN a `category_translation` y, si no hay traducción, el nombre original.
- Las columnas `product_name_lenght` y `product_description_lenght` tienen "length" mal escrito en el dataset original.

## Tabla order_reviews

Una fila por reseña que deja el cliente después de recibir la orden.
- `review_score`: puntaje de 1 a 5.
- `review_comment_title`, `review_comment_message`: texto opcional, en portugués.
- Algunas órdenes tienen más de una reseña: para promediar puntaje por orden, primero promediar por `order_id`.

## Tabla geolocation

Coordenadas (latitud y longitud) por prefijo de código postal. Tiene muchas filas repetidas por prefijo. Solo se usa para mapas; para análisis por región o estado usar directamente `customer_state` o `seller_state`.

## Métrica: facturación o ventas (GMV)

Cómo calcular cuánto vendió o facturó la empresa, cuánto dinero ingresó, y cómo comparar ventas entre regiones, estados, categorías, vendedores o períodos (cuánto crecieron o cayeron las ventas).

Cuando se pregunta por "ventas", "facturación", "ingresos" o "revenue" sin más detalle, usar el GMV (valor bruto de mercadería):
- GMV = SUM(order_items.price + order_items.freight_value)
- Solo órdenes que cuentan como venta (estado distinto de `canceled` y `unavailable`).
- Fecha: `orders.order_purchase_timestamp`.
- Si se pide explícitamente "sin flete" o "ingresos por producto", usar solo SUM(order_items.price).

## Métrica: cantidad de órdenes y ticket promedio

- Cantidad de órdenes = COUNT(DISTINCT orders.order_id) de órdenes que cuentan como venta.
- Ticket promedio (AOV) = GMV / cantidad de órdenes.
- Ítems por orden = COUNT(order_items) / COUNT(DISTINCT order_id).

## Métrica: tiempo de entrega y atrasos

- Tiempo de entrega en días = `order_delivered_customer_date` - `order_purchase_timestamp`, expresado en días. Usar EXTRACT(EPOCH FROM ...) / 86400.
- Una orden está atrasada si `order_delivered_customer_date` > `order_estimated_delivery_date`.
- Tasa de atraso = órdenes atrasadas / órdenes entregadas.
- Solo órdenes con estado `delivered` y fecha de entrega no nula.

## Métrica: satisfacción del cliente

- Puntaje promedio = AVG(order_reviews.review_score).
- NPS simplificado: promotores = puntaje 5, detractores = puntaje 1 a 3. NPS = (% promotores - % detractores).
- Una reseña negativa es una con `review_score` <= 2.

## Métrica: clientes recurrentes

- Un cliente es recurrente si su `customer_unique_id` tiene más de una orden.
- Tasa de recompra = clientes con 2 o más órdenes / clientes únicos totales.
- En este dataset la recompra es muy baja (alrededor del 3%).

## Regiones de Brasil

Cuando se pregunta por "región", agrupar los estados (`customer_state` o `seller_state`) en las 5 regiones oficiales de Brasil:
- Norte: AC, AP, AM, PA, RO, RR, TO
- Nordeste: AL, BA, CE, MA, PB, PE, PI, RN, SE
- Centro-Oeste: DF, GO, MT, MS
- Sudeste: ES, MG, RJ, SP
- Sul: PR, RS, SC
Se implementa con un CASE WHEN sobre la sigla del estado. Si se pregunta por "estado", no agrupar: usar la sigla directamente. Las regiones de ventas se definen por la ubicación del cliente salvo que se pregunte por vendedores.

## Estacionalidad y eventos conocidos

- Noviembre de 2017 tiene un pico de ventas por Black Friday (24 de noviembre de 2017).
- Las ventas crecieron fuertemente durante 2017 y se estabilizaron en 2018.
- Para comparar crecimiento entre años, comparar los mismos meses (por ejemplo enero a agosto de 2017 contra enero a agosto de 2018).
