# Roadmap, Pilotos e Validação

## 1. Estratégia de execução

O risco principal não é fazer um mapa na web. É provar que o MDT, a água calculada e as linhas propostas correspondem ao campo. Por isso o roadmap começa com dados e modo sombra, não com automação total.

Horizonte realista:

- Diagnóstico interno confiável: 4 a 6 meses após a descoberta.
- Gerador assistido de sulcação: 7 a 10 meses.
- Projeto conservacionista validado em campo: uma safra/reforma acompanhada.
- Produto robusto multiempresa: 12 a 18 meses.

## 2. Fase 0 — descoberta e conjunto ouro

**Duração:** 6 a 8 semanas.
**Objetivo:** reduzir incerteza técnica antes de investir no produto inteiro.

### Entregas

- Entrevistas com agrônomos, topógrafos, mecanização e gestores.
- 10 a 20 projetos históricos completos.
- Cinco áreas escolhidas como golden datasets.
- Matriz cobrindo relevo, solo, cobertura, método de voo e frota.
- Contrato de dados validado.
- Inventário de formatos de controladores/máquinas.
- Taxonomia de estruturas, achados e violações.
- Primeiro pacote regional de regras.
- Métricas de baseline: tempo, retrabalho, manobras, risco e custo.
- Parecer de atribuições profissionais e fluxo de ART.

### Go/no-go

Avançar somente se:

- Houver pelo menos três casos com MDT, checkpoints, projeto e evidência de campo.
- Dois especialistas conseguirem chegar a critérios comparáveis.
- Os clientes aceitarem fornecer solo, chuva e estruturas além do polígono.
- Houver acesso a dados de montante/jusante.
- Um formato inicial de exportação puder ser testado em equipamento real.

## 3. Fase 1 — fundação e diagnóstico

**Duração:** 10 a 14 semanas.
**Objetivo:** transformar dados reais em diagnóstico reproduzível.

### Escopo

- Organização, projeto, permissões e auditoria básica.
- Upload multipart e datasets imutáveis.
- Validação de segurança, CRS, datum, cobertura e qualidade.
- Normalização COPC/COG.
- Importação de MDT pronto e derivação assistida de nuvem.
- Central de qualidade, hillshade e perfis.
- MDT original/QC/hidrocondicionado.
- Declividade, fluxo, microbacias, entradas, saídas e depressões.
- Cadastro de bueiros, breaklines e estruturas.
- RUSLE preliminar com proveniência.
- Relatório interno de diagnóstico.

### Fora do escopo

- Fotogrametria de imagens brutas.
- Projeto executivo automático.
- HEC-RAS em cada projeto.
- Integrações OEM.
- Aplicativo mobile completo.

### Aceitação

- Cem por cento dos outputs referenciam inputs, parâmetros e versão.
- Casos com CRS/datum ausente são bloqueados.
- Microbacias de borda recebem alerta correto.
- Fluxos principais concordam com o conjunto ouro e vistoria dentro da meta do piloto.
- Jobs são retomáveis/idempotentes.
- Nenhum resultado de demonstração aparece como cálculo real.

## 4. Fase 2 — projeto assistido

**Duração:** 12 a 16 semanas.
**Objetivo:** gerar e editar alternativas de conservação e sulcação.

### Escopo

- Biblioteca versionada de solo, chuva, manejo e máquinas.
- Pedido de geração resolvido, catálogo de interferências e perfis da frota do ciclo.
- Configurador de evento com IDF, duração, TR e hietograma.
- Escoamento simplificado/SIMWE e balanço.
- PCE e PCX digitais.
- Linhas candidatas de terraço/canal.
- Blocos, carreadores e cabeceiras.
- Linhas-mestras, offsets, clipping e perfis.
- Quebras determinísticas pelo `power_line_axis` e demais barreiras.
- Massa por tiro, POAs candidatos/atuais, rotas e capacidade logística estática.
- Restrições duras e violações georreferenciadas.
- Três a oito alternativas Pareto.
- Editor técnico e justificativa de override.
- Exportação preliminar GeoPackage/DXF/SHP/KML/PDF.
- Revisão humana obrigatória.

### Aceitação

- Cem por cento das geometrias satisfazem as restrições duras declaradas.
- Nenhuma alternativa sem saída protegida pode ser selecionada.
- Alterar MDT/solo/chuva invalida cenários dependentes.
- Alterar interferência, frota, produtividade ou POA invalida sulcos/rotas/logística dependentes.
- Nenhuma linha, portal, rota ou POA intercepta a faixa elétrica V1.
- Dois especialistas revisam as alternativas sem conhecer a escolhida pelo algoritmo.
- Tempo técnico por hectare cai sem aumentar omissões críticas.

## 5. Fase 3 — pilotos supervisionados

**Duração:** 6 a 9 meses, acompanhando execução e chuva.
**Objetivo:** provar utilidade e segurança no campo.

### Desenho do piloto

- Cinco a dez áreas.
- Pelo menos três classes de solo.
- Relevo suave, médio e situação desafiadora.
- Solo exposto e cobertura/vegetação em alguns levantamentos.
- Fotogrametria RTK e, quando possível, LiDAR/GNSS terrestre.
- Diferentes larguras, raios e controladores.
- Eventos de chuva observados.
- Áreas com e sem rede elétrica, POAs e dados CTT observáveis.

### Etapas

1. Projeto convencional independente.
2. Execução do TerraFlux em modo sombra.
3. Comparação cega por dois especialistas.
4. Correções e registro das causas.
5. Execução supervisionada de áreas aprovadas.
6. As built com GNSS.
7. Inspeção após primeira chuva relevante.
8. Nova inspeção após evento intenso.
9. Calibração e publicação do rule pack regional.
10. Comparação de massa, rotas, filas, tráfego e entrega contra telemetria CTT.

### Evidências

- Checkpoints e resíduos do MDT.
- Caminhos de água observados.
- Profundidade/velocidade ou marcas de escoamento onde possível.
- Erosões novas e antigas.
- Extravasamentos e assoreamento.
- Diferença de locação e seção.
- Desempenho da máquina, manobras e linhas curtas.
- Área útil e tempo operacional.
- POAs, quilômetros vazio/carregado, esperas, filas e regularidade de entrega.
- Horas de revisão e retrabalho.

### Go/no-go para executivo

- Nenhuma falha crítica de saída/estrutura não detectada.
- Concordância mínima definida com especialistas para achados de risco.
- Erro de locação dentro da tolerância profissional.
- Balanço hidráulico e sensibilidade aprovados.
- Formato testado no controlador real.
- Processo de revisão/ART validado juridicamente.
- Plano de manutenção aceito pelo cliente.

## 6. Fase 4 — produto e escala

- Multiempresa e portfólio.
- Billing, quotas, SLA e suporte.
- Aplicativo de campo offline.
- Aprovação e assinatura.
- Adaptadores OEM por modelo/firmware.
- APIs e webhooks.
- As built e monitoramento pós-implantação.
- Alta disponibilidade e disaster recovery.
- Pacotes regionais adicionais.
- Hidráulica 2D em hotspots.

## 7. Equipe

### Núcleo mínimo do MVP

| Papel | Dedicação |
|---|---:|
| Product/domain lead | 1,0 |
| Engenheiro agrônomo de solo e água | 1,0 |
| Engenheiro geoespacial/hidrologia | 1,0 |
| Backend geoespacial | 1,0–2,0 |
| Frontend GIS | 1,0 |
| Plataforma/data/DevOps | 0,5–1,0 |
| QA geoespacial | 0,5–1,0 |
| Designer/pesquisa | 0,5 |

Apoios recorrentes: topógrafo, especialista em mecanização, jurídico ambiental/profissional e operador de drone.

Uma equipe menor pode construir uma prova de conceito, mas não deve validar sozinha regras agronômicas nem exportação para execução.

## 8. Orçamento de planejamento

Valores são faixas para decisão, não cotações. Precisam ser recalculados com região, salários, contratação, impostos e nuvem escolhida.

| Marco | Prazo | Faixa estimada |
|---|---:|---:|
| Descoberta, dados e especificação | 6–8 semanas | R$ 150 mil–350 mil |
| MVP de diagnóstico confiável | 4–6 meses | R$ 650 mil–1,4 milhão |
| Motor assistido + pilotos | +6–9 meses | Acumulado R$ 2–4 milhões |
| Produto robusto validado | 12–18 meses | Acumulado R$ 4–8 milhões |

Infraestrutura ilustrativa:

- Piloto pequeno: US$ 250–700/mês.
- Produção inicial: US$ 800–2.000/mês.
- Alta disponibilidade/múltiplos jobs: US$ 1.500–5.000/mês.

Fotogrametria de imagens brutas, transferência, suporte e retenção de históricos podem dominar o custo. Medir custo por projeto desde o primeiro job.

## 9. Backlog priorizado

### Must have

- Contrato de dados e prontidão.
- Pedido de geração, catálogo de parâmetros e manifesto resolvido.
- Upload retomável e segurança.
- CRS/datum/QA/checkpoints.
- MDT auditável e editor de conectividade.
- Microbacia inteira, entradas e saídas.
- PCE/RUSLE separado de PCX/evento.
- Cadastro de solo/chuva/manejo/máquina.
- Declaração/catálogo de interferências e recorte pelo `power_line_axis`.
- Frota completa do ciclo, produtividade e rede dirigível.
- POA estático: candidatos, capacidade, rotas e tráfego.
- Perfis e achados georreferenciados.
- Gerador assistido e verificadores.
- Proveniência, versões, revisão e bloqueio de exportação.

### Should have

- 3D real e nuvem COPC.
- SIMWE e sensibilidade.
- Pareto e custos.
- Campo offline.
- As built.
- Adaptadores prioritários.
- Rule packs regionais.
- POA integrado, despacho e simulação de filas/falhas.

### Could have

- HEC-RAS automatizado em hotspots.
- Fotos brutas por integração.
- WEPP.
- Travessias elétricas 3D especiais com procedimento aprovado.
- Detecção de feições por IA.
- Monitoramento automático pós-chuva.

### Não fazer agora

- IA generativa decidindo hidráulica.
- Projeto executivo sem profissional.
- Marketplace amplo de equipamentos.
- Solver próprio de fotogrametria ou CFD.
- Aplicativo mobile igual ao editor desktop.

## 10. Matriz de testes

### Dados sintéticos

- Plano inclinado: direção e declividade conhecidas.
- Vale em V: convergência e talvegue.
- Crista: divisão de bacias.
- Depressão real: volume e retenção.
- Estrada com/sem bueiro: conectividade.
- Ruído/faixas: QA da nuvem.
- Vegetação: diferença MDS/MDT.
- Barreira linear: sulcos, alcance e rota quebrados sem atravessar o buffer.
- Rede logística pequena: solução de POA e rotas com ótimo conhecido.

### Golden datasets

- Outputs numéricos versionados.
- Tolerância por métrica.
- Comparação após mudança de biblioteca/worker.
- Revisão visual e perfis.

### Campo

- Pontos GNSS independentes.
- Saídas e entradas vistoriadas.
- Marcas/erosões após chuva.
- Estruturas existentes e as built.
- Execução real das linhas.
- Eixo de rede elétrica, locais de POA e rotas CTT conferidos.
- Tempos de enchimento, viagem, fila, descarga e entrega observados.

### Produto

- Upload interrompido e retomado.
- Dataset inválido com explicação.
- Job repetido/idempotente.
- Alteração que invalida cenário.
- Override com justificativa.
- Bloqueio de exportação.
- Isolamento entre organizações.

## 11. Métricas e metas do piloto

As metas numéricas devem ser fechadas na Fase 0. Categorias obrigatórias:

- Precisão vertical e horizontal.
- Recall/precision dos pontos críticos.
- Concordância de microbacias e exutórios.
- Erro de volume/vazão em casos calibrados.
- Violações geométricas por 100 km.
- Diferença projeto/as built.
- Minutos técnicos por hectare.
- Retrabalho evitado.
- Área útil e manobras.
- Quilômetros vazio/carregado por tonelada, espera e fila P95.
- Intensidade de tráfego, passadas repetidas e regularidade da entrega.
- Custo de compute e storage por hectare.
- Satisfação/confiança do responsável.

North star: hectares implantados e aprovados sem redesenho crítico.

## 12. Registro de riscos

| Risco | Prob. | Impacto | Resposta |
|---|---:|---:|---|
| Cliente envia ortomosaico sem MDT | Alta | Alto | Níveis de entrega e plano de novo levantamento |
| Cana impede ver o solo | Alta | Alto | Época de voo, LiDAR ou RTK complementar |
| Bacia ultrapassa propriedade | Alta | Alto | MDE regional + extensão do levantamento + bloqueio |
| Solo/infiltração desconhecidos | Alta | Alto | Faixas apenas no preliminar; ensaio no executivo |
| Especialistas discordam | Média | Alto | Rule pack versionado e comitê técnico |
| Modelo mostra falsa precisão | Média | Crítico | Incerteza, sensibilidade e gates |
| Exportação causa erro de máquina | Média | Crítico | Certificação por controlador/firmware |
| Rede elétrica ausente do LAZ/shape | Alta | Crítico | Declaração obrigatória, barreira fechada e vistoria |
| POA ótimo no mapa falha no solo/drenagem | Média | Crítico | Polígono, sondagem, drenagem, acesso e revisão de campo |
| Frota CTT desbalanceada cria filas | Alta | Alto | Capacidade temporal, simulação e contingência |
| Estrutura falha por manutenção | Média | Crítico | Plano, as built e inspeção pós-chuva |
| Custo de processamento cresce | Média | Médio | Resolução adaptativa, cache e quotas |
| Responsabilidade jurídica ambígua | Média | Crítico | Posicionamento, contratos, ART e trilha |
| Dados sensíveis vazam | Baixa/média | Alto | RLS, KMS, objetos privados e auditoria |

## 13. Plano dos próximos 30 dias

### Semana 1

- Nomear líder agronômico e líder geoespacial.
- Escolher região inicial.
- Listar 20 projetos históricos candidatos.
- Definir modelo de termo de compartilhamento de dados.

### Semana 2

- Receber três conjuntos completos.
- Preencher o manifesto de cada levantamento.
- Rodar auditoria manual de CRS, datum, cobertura e checkpoints.
- Entrevistar planejamento mecanizado e operação.
- Inventariar redes elétricas, POAs, carreadores, frota CTT e dados de produtividade.

### Semana 3

- Reproduzir MDT, fluxo e perfis em ferramentas existentes.
- Comparar com projeto histórico e campo.
- Catalogar regras, exceções e divergências entre especialistas.
- Escolher formatos de exportação iniciais.
- Montar um caso histórico com sequência, filas e telemetria de transbordo/caminhão.

### Semana 4

- Fechar critérios do golden dataset.
- Priorizar backlog da Fase 1.
- Estimar infraestrutura com o tamanho real dos arquivos.
- Aprovar orçamento e critérios de go/no-go.

Ao final de 30 dias deve existir um dossiê de três áreas reais e uma decisão fundamentada sobre iniciar o MVP, ajustar o escopo ou interromper.
