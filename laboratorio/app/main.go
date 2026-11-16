package main

import (
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	// Métrica RED padrão: Duração da requisição HTTP (Histograma com buckets de latência)
	httpDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "Duracao das requisicoes HTTP em segundos.",
			Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5},
		},
		[]string{"path", "status"},
	)

	// Contador de requisições totais por status
	httpRequestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "Total de requisicoes HTTP processadas.",
		},
		[]string{"path", "status"},
	)

	// Métrica de alta cardinalidade (simulando múltiplos sensores/dispositivos IoT na borda)
	edgeSensorGauge = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "edge_iot_sensor_reading",
			Help: "Leitura simulada de sensores IoT conectados ao no de borda (alta cardinalidade).",
		},
		[]string{"sensor_id", "zone", "protocol", "device_type"},
	)

	// Variáveis atômicas para injeção de caos controlado via API
	artificialDelayMs int64 = 0
	errorRatePercent  int64 = 0
	cardinalitySeries int64 = 0
)

func init() {
	prometheus.MustRegister(httpDuration)
	prometheus.MustRegister(httpRequestsTotal)
	prometheus.MustRegister(edgeSensorGauge)
}

func workHandler(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	status := http.StatusOK

	// Injeção de taxa de erro controlada (Cenário 3)
	rate := atomic.LoadInt64(&errorRatePercent)
	if rate > 0 && rand.Int63n(100) < rate {
		status = http.StatusInternalServerError
	}

	// Injeção de atraso artificial de latência (Cenários 1 e 2)
	delay := atomic.LoadInt64(&artificialDelayMs)
	if delay > 0 {
		time.Sleep(time.Duration(delay) * time.Millisecond)
	} else {
		// Simulação de processamento mínimo realista de CPU (2 a 5ms)
		time.Sleep(time.Duration(2+rand.Intn(4)) * time.Millisecond)
	}

	w.WriteHeader(status)
	if status == http.StatusOK {
		fmt.Fprintf(w, `{"status":"ok","latency_ms":%d}`, time.Since(start).Milliseconds())
	} else {
		fmt.Fprintf(w, `{"status":"error","code":500}`)
	}

	duration := time.Since(start).Seconds()
	statusStr := strconv.Itoa(status)
	httpDuration.WithLabelValues("/work", statusStr).Observe(duration)
	httpRequestsTotal.WithLabelValues("/work", statusStr).Inc()

	// Se alta cardinalidade estiver ativa, atualiza subconjunto de séries a cada requisição
	card := atomic.LoadInt64(&cardinalitySeries)
	if card > 0 {
		idx := rand.Int63n(card)
		sensorID := fmt.Sprintf("sensor_%04d", idx)
		zone := fmt.Sprintf("zone_%02d", idx%10)
		edgeSensorGauge.WithLabelValues(sensorID, zone, "mqtt_http", "industrial_temp").Set(20.0 + rand.Float64()*15.0)
	}
}

// Endpoints para controle remoto de injeção de falhas pelo K6 ou orquestrador
func chaosHandler(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	if d := query.Get("delay_ms"); d != "" {
		val, _ := strconv.ParseInt(d, 10, 64)
		atomic.StoreInt64(&artificialDelayMs, val)
	}
	if e := query.Get("error_rate"); e != "" {
		val, _ := strconv.ParseInt(e, 10, 64)
		atomic.StoreInt64(&errorRatePercent, val)
	}
	if c := query.Get("cardinality"); c != "" {
		val, _ := strconv.ParseInt(c, 10, 64)
		atomic.StoreInt64(&cardinalitySeries, val)
		edgeSensorGauge.Reset()
		for i := int64(0); i < val; i++ {
			sensorID := fmt.Sprintf("sensor_%04d", i)
			zone := fmt.Sprintf("zone_%02d", i%10)
			edgeSensorGauge.WithLabelValues(sensorID, zone, "mqtt_http", "industrial_temp").Set(22.5 + float64(i%10)*0.5)
		}
	}
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"current_delay_ms":%d,"current_error_rate":%d,"current_cardinality":%d}`,
		atomic.LoadInt64(&artificialDelayMs), atomic.LoadInt64(&errorRatePercent), atomic.LoadInt64(&cardinalitySeries))
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status":"healthy"}`))
}

func main() {
	http.HandleFunc("/work", workHandler)
	http.HandleFunc("/chaos", chaosHandler)
	http.HandleFunc("/health", healthHandler)
	http.Handle("/metrics", promhttp.Handler())

	port := ":8080"
	log.Printf("API Alvo de Benchmark iniciada na porta %s\n", port)
	if err := http.ListenAndServe(port, nil); err != nil {
		log.Fatalf("Falha ao iniciar servidor: %v", err)
	}
}
