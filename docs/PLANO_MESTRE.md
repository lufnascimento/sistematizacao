# Plano Mestre do TerraFlux

**Versão:** 1.0
**Data:** 11 de agosto de 2026
**Status:** definição de produto e engenharia para validação com especialistas

Documentos complementares que detalham esta visão:

- [Métodos conservacionistas regionais](METODOS_CONSERVACIONISTAS_REGIONAIS.md)
- [Lógica do ESD e do Canal Escoadouro](LOGICA_ESD_E_CANAL_ESCOADOURO.md)
- [Motor de cenários, terraços e sulcação](MOTOR_DE_CENARIOS_E_SULCACAO.md)
- [Produto analítico de sulcação](PRODUTO_ANALITICO_DE_SULCACAO.md)
- [Catálogo de produtos e níveis de liberação](CATALOGO_PRODUTOS.md)

## 1. Resumo executivo

O TerraFlux deve ser uma estação de projeto para transformar topografia, água, solo, restrições ambientais e capacidade das máquinas em alternativas de sistematização de canaviais.

### Decisão de viabilidade

**Sim, o produto funciona**, desde que seja posicionado como diagnóstico e projeto assistido, com bloqueios de qualidade e profissional no circuito.

**Não funciona com segurança** como ferramenta de “um clique” que recebe apenas o polígono e qualquer imagem de drone e entrega um projeto pronto para a máquina.

| Situação | Resultado permitido |
|---|---|
| Polígono apenas | Cadastro, área, contexto público e triagem de baixa confiança |
| Polígono + ortomosaico RGB | Fotointerpretação, obstáculos visíveis e desenho manual |
| Polígono + MDS | Triagem de relevo somente se o solo estiver exposto e a qualidade for demonstrada |
| Polígono + MDT validado | Diagnóstico topográfico e hídrico |
| Polígono + nuvem classificada e checkpoints | Geração e auditoria do MDT, perfis e locação preliminar |
| MDT + solo + chuva + microbacia + estruturas + máquinas | Anteprojeto e alternativas conservacionistas |
| Dados anteriores + vistoria + memória + aprovação profissional | Projeto liberável para execução |

O ortomosaico é imagem 2D. A aerofotogrametria pode produzir ortomosaico, MDS, MDT e curvas, mas são produtos diferentes; o manual do Incra indica que feições como divisores, talvegues e áreas alagadiças exigem combinação com dados de elevação e controle de precisão. [Incra, Manual Técnico de Georreferenciamento](https://www.gov.br/incra/pt-br/assuntos/governanca-fundiaria/Manual_Tecnico_de_Georreferenciamento_2_Edicao.pdf/%40%40display-file/file)

## 2. Tese do produto

O diferencial não é desenhar linhas paralelas. Softwares de SIG e CAD já fazem isso. O diferencial defensável é manter, numa única cadeia reproduzível:

1. Qualidade e linhagem dos dados.
2. Modelo de terreno hidrologicamente coerente.
3. Diagnóstico da água na microbacia inteira.
4. Projeto de controle da erosão e da enxurrada.
5. Regras agronômicas regionais e versionadas.
6. Geração mecanizável de terraços, canais, carreadores e sulcação.
7. Comparação multicritério com incerteza explícita.
8. Revisão em escritório, vistoria, aprovação e as built.

O IAC organiza o planejamento conservacionista em Projeto de Controle da Erosão (PCE) e Projeto de Controle da Enxurrada (PCX). Essa separação deve virar a espinha dorsal funcional do TerraFlux. [IAC, Boletim Técnico 216](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf)

## 3. Princípios obrigatórios

### 3.1 Água primeiro, operação depois

A sequência não pode ser invertida:

1. Restrições ambientais, jurídicas e interferências físicas.
2. Microbacias, contribuições externas e saídas.
3. Práticas de cobertura e infiltração.
4. Terraços, retenção, canais e descargas.
5. Estradas, carreadores e cabeceiras.
6. Linhas-mestras e sulcos paralelos.
7. Massa por tiro, saídas, rotas, POAs e capacidade da colheita.
8. Nova simulação de água, tráfego e logística com as geometrias propostas.

### 3.2 O talhão não é a bacia

O polígono administrativo pode cortar a microbacia. Água pode entrar por área vizinha, estrada ou bueiro e sair fora da área levantada. O sistema deve detectar contribuição cruzando a borda e bloquear o dimensionamento quando montante ou jusante forem desconhecidos.

### 3.3 Nenhuma saída, nenhum projeto

Uma estrutura de drenagem ou sulco não pode terminar em estrada, talude, APP, imóvel vizinho ou encosta sem proteção. Todo caminho relevante deve possuir destino identificado e capacidade verificada.

### 3.4 Medido, informado, inferido e padrão são coisas diferentes

Todo parâmetro deve possuir:

- Valor e unidade.
- Fonte.
- Geometria ou abrangência.
- Data e validade.
- Método de obtenção.
- Responsável.
- Grau de confiança.
- Versão usada no cálculo.

O pedido canônico separa fatos informados, medições, valores calculados, catálogo, `rule_pack`, preferências do otimizador e hipóteses E0. Limites de segurança, permissões, capacidade hidráulica e afastamento de infraestrutura não são pesos livres. A política completa está em [Auditoria de gaps, parâmetros e interferências](./AUDITORIA_DE_GAPS_E_PARAMETROS.md).

### 3.5 Uma nota única não representa confiança

O produto deve mostrar separadamente:

- **Qualidade do dado:** topografia, cobertura, checkpoint e coerência.
- **Incerteza do modelo:** sensibilidade a chuva, infiltração, resolução e método.
- **Adequação agronômica:** cumprimento das regras conservacionistas.
- **Status profissional:** calculado, revisado, vistoriado, aprovado ou liberado.

## 4. Níveis de entrega

### L0 — Derivados do levantamento

**Entradas:** LAZ/LAS, MDT/MDE, ortomosaico ou vetores recebidos.
**Entrega:** QA, densidade, superfícies, relevo, curvas e derivados geométricos com parâmetros registrados.
**Não entrega:** acurácia absoluta sem checkpoints, interpretação hidrológica ou recomendação executiva.

### L1 — Triagem

**Entradas:** polígono, ortomosaico e dados públicos.
**Entrega:** inventário visual, restrições aparentes, relevo regional, perguntas e plano de levantamento.
**Não entrega:** cotas executivas, vazão, seção hidráulica ou linhas para piloto automático.

### L2 — Planejamento/anteprojeto condicionado

**Entradas:** MDT ou nuvem adequada, chuva e solo medidos ou inferidos com fonte, contexto da bacia, estruturas, interferências declaradas e máquinas. Para POA dimensionado: produtividade, rede dirigível e frota CTT.
**Entrega:** diagnóstico, RUSLE, escoamento de evento simplificado, alternativas de estruturas e sulcação, triagem/planejamento de POA conforme os insumos, perfis, custos e incerteza.
**Marca:** “preliminar, não liberar para máquina”.

### L3 — Projeto técnico validado

**Entradas:** topografia validada por pontos independentes, levantamento de montante/jusante, solo e infiltração representativos, IDF, estruturas vistoriadas, frota, permissões e responsável técnico.
**Entrega:** geometrias aprovadas, dimensionamento, memória de cálculo, arquivos de locação, checklist de campo e versão assinada.
**Dependência:** validação de atribuições profissionais e ART quando o trabalho constituir serviço técnico contratado. A Lei 6.496/1977 vincula serviços profissionais de Engenharia e Agronomia à ART. [Lei 6.496/1977](https://planalto.gov.br/ccivil_03/leis/l6496.htm)

## 5. Usuários e responsabilidades

| Persona | Decisão principal |
|---|---|
| Gestor agrícola | Custo, risco, área útil, prazo e cenário |
| Engenheiro agrônomo responsável | Premissas, práticas, parâmetros, aprovação e ART |
| Especialista em solo e água | Infiltração, erodibilidade, chuva e hidráulica |
| Analista GIS/topógrafo | CRS, datum, nuvem, MDT, checkpoints e locação |
| Planejador de mecanização | Frota, raio, espaçamento, cabeceira e eficiência |
| Planejador CTT/logística | Produtividade, sequência, transbordos, POAs, caminhões, filas e entrega |
| Técnico de campo | Vistoria, fotos, pontos críticos, execução e as built |
| Revisor/auditor | Linhagem, versões, justificativas e conformidade |

O cliente inicial mais adequado são usinas, grupos agrícolas e consultorias especializadas. O autosserviço para pequeno produtor deve vir depois, pois levantamento, suporte e responsabilidade tornam o fluxo técnico mais complexo que um SaaS convencional.

## 6. Jornada completa

1. Criar organização, fazenda, bloco, safra e objetivo da reforma.
2. Desenhar ou subir os talhões.
3. Escolher a rota de entrada: MDT, nuvem, ortomosaico ou dados de voo.
4. Fazer upload retomável e registrar metadados.
5. Executar validação de segurança, formato, CRS, datum, cobertura e precisão.
6. Classificar o resultado como triagem, anteprojeto ou elegível ao executivo.
7. Completar cadastro de solo, chuva, manejo, estruturas, restrições e todas as máquinas do ciclo.
8. Enviar `power_line_axis` pelo centro dos postes ou declarar formalmente que não há rede; classificar demais interferências.
9. Gerar e revisar o MDT; cadastrar breaklines, bueiros e depressões reais.
10. Delimitar microbacias e confirmar entradas e saídas de água.
11. Rodar PCE: erosão média, cobertura, preparo, palha, rampa e práticas.
12. Rodar PCX: evento, volume, pico, velocidade, armazenamento e descarga.
13. Definir receptores e gerar estruturas conservacionistas candidatas.
14. Definir ou readequar drenagem, carreadores, blocos e cabeceiras.
15. Gerar linhas de controle, quando necessárias, e todas as linhas físicas de sulcação.
16. Quebrar sulcos, alcances e rotas em barreiras; recalcular a água.
17. Calcular massa por tiro, eventos de enchimento, POAs, rotas e capacidade logística.
18. Realimentar sulcação, saídas e carreadores e recalcular água/tráfego.
19. Exibir alternativas Pareto: conservação, equilíbrio e operação.
20. Editar com verificadores ativos e justificativa para exceções.
21. Emitir pacote de vistoria e coletar evidências offline.
22. Recalcular, revisar e aprovar.
23. Exportar pacote versionado para SIG, CAD e máquina.
24. Registrar locação, execução e levantamento as built.
25. Inspecionar após chuva e operação e alimentar a calibração.

## 7. Motores científicos

### 7.1 Qualidade topográfica

O sistema deve validar CRS horizontal, unidade, datum vertical, cobertura, densidade, pontos de solo, ruído, vazios, faixas de voo e resíduos nos checkpoints.

Altitudes GNSS são geométricas e não devem ser silenciosamente tratadas como altitudes físicas. O IBGE fornece o hgeoHNOR2020 para conversão compatível com o Sistema Geodésico Brasileiro. [IBGE, hgeoHNOR2020](https://www.ibge.gov.br/geociencias/modelos-digitais-de-superficie/modelos-digitais-de-superficie/31283-hgeohnor2020-modeloconversaoaltitudesgeometricasgnss-datumverticalsgb.html)

Regra de bloqueio: se o erro vertical for comparável à queda que a estrutura pretende impor ou medir, o dado não sustenta o projeto. Uma linha com 0,3% cai apenas 0,30 m em 100 m.

### 7.2 Diagnóstico do terreno

Produtos mínimos:

- MDT original, corrigido e hidrocondicionado, sempre separados.
- Declividade, aspecto e curvaturas.
- Direção e acumulação de fluxo.
- Microbacias, divisores, talvegues e exutórios.
- Comprimento hidráulico de rampas.
- Depressões com profundidade e volume.
- Entradas externas e destinos a jusante.
- Perfis longitudinal e transversal.
- Mapa de confiança e influência da resolução.

Estradas sem bueiro aberto no MDT viram barragens digitais falsas. Preencher todas as depressões pode apagar baixadas e áreas úmidas reais. Correções precisam ser explícitas, editáveis e versionadas.

### 7.3 Erosão média

Usar RUSLE como ferramenta de triagem e comparação:

`A = R × K × LS × C × P`

- `R`: erosividade da chuva.
- `K`: erodibilidade do solo.
- `LS`: comprimento e declividade da rampa.
- `C`: cultura, cobertura e manejo.
- `P`: prática conservacionista.

RUSLE estima perda média por erosão laminar e em sulcos. Não dimensiona canais, não representa uma tempestade específica e não resolve adequadamente fluxo concentrado, voçorocas ou deposição. O produto deve manter “perda média” separada de “hidráulica do evento”. [ANA, Manual de Diagnóstico](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-2)

### 7.4 Escoamento de evento

Entradas:

- IDF local, duração, tempo de retorno e hietograma.
- Infiltração ou parâmetros rastreáveis.
- Umidade antecedente.
- Cobertura e Manning.
- Microbacia completa.
- Estruturas e condições de saída.

Saídas:

- Chuva excedente.
- Volume escoado.
- Vazão de pico e hidrograma.
- Lâmina, velocidade e energia.
- Tempo ao pico.
- Armazenamento, extravasamento e falha.
- Balanço de massa e sensibilidade.

O método CN pode estimar volume; o Racional pode estimar pico em pequenas bacias sob hipóteses restritivas; Manning verifica seções e velocidade. Modelos e limites de aplicação devem aparecer na memória. As IDFs devem ter fonte rastreável, preferindo o [Atlas Pluviométrico do SGB](https://www.sgb.gov.br/publicacoes-atlas-pluviometrico-e-estudos-de-chuvas-intensas) e séries do [Hidroweb/ANA](https://www.ana.gov.br/hidrowebservice/swagger-ui/index.html).

## 8. Recomendador agronômico

O recomendador deve combinar três famílias, nunca sugerir uma obra isolada:

### Cobrir e proteger

- Manutenção de palha.
- Adubação verde e cobertura no período de reforma.
- Reforma em faixas/blocos para reduzir exposição simultânea.
- Proteção vegetal de canais, saídas e áreas vulneráveis.

### Infiltrar quando o perfil permitir

- Preparo mínimo ou localizado.
- Controle de tráfego.
- Operação na umidade adequada.
- Correção de compactação apenas após diagnóstico.
- Retenção/infiltração apenas com capacidade e esvaziamento verificados.

### Conduzir com segurança quando necessário

- Sulcação próxima ao contorno ou em gradiente controlado.
- Terraços de infiltração ou drenagem conforme perfil e chuva.
- Canais vegetados e desvios.
- Dissipadores e descargas estáveis.
- Tratamento de estradas e carreadores como parte da drenagem.

A Embrapa ressalta que conservação na cana envolve tipo de solo, preparo, traçado, cobertura, tamanho de talhões e terraceamento em conjunto. [Embrapa, manejo e conservação da cana](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/correcao-e-adubacao/manejo-e-conservacao)

## 9. Geração de estruturas e sulcação

### 9.1 Terraços e canais

1. Identificar rampas, áreas contribuintes e vazões.
2. Selecionar candidatos por pacote regional de regras.
3. Criar alinhamentos próximos ao contorno ou ao gradiente admissível.
4. Calcular espaçamento por solo, chuva, manejo, tolerância e capacidade.
5. Dimensionar seção, bordo livre, velocidade e esvaziamento.
6. Conectar somente a saída protegida.
7. Verificar corte, aterro, tráfego e manutenção.
8. Inserir a estrutura na superfície proposta.
9. Simular novamente e testar falha/bloqueio parcial.

Declividade isolada nunca deve determinar tipo ou espaçamento. A Embrapa relaciona o dimensionamento à chuva, infiltração, solo, manejo, declividade e capacidade da seção. [Embrapa, terraceamento](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/arroz/producao/sistema-de-cultivo/arroz-de-terras-altas/terraceamento)

### 9.2 Linhas de sulcação

Restrições duras:

- Área útil e exclusões respeitadas.
- Sem descarga desprotegida.
- Sem alinhamento morro abaixo fora de condição aprovada.
- Declividade longitudinal e comprimento hidráulico válidos.
- Cruzamento de canal/terraço apenas em ponto projetado.
- Espaçamento, curvatura, bitola e raio compatíveis.
- Cabeceira suficiente.
- Sem cruzamentos, sobreposições ou fragmentos impraticáveis.
- Sem concentração perigosa em cabeceira, estrada ou vizinho.
- `POWER_LINE_AXIS` e demais barreiras recortam a área antes da geração; na V1 não há travessia automática sob rede.

No ESD, uma linha de controle não será presumida como canal físico. A família completa de sulcos será acoplada à superfície e aos receptores, e os grafos operacional e hidráulico serão calculados separadamente. Todo alcance precisa terminar em receptor estável; CEV e estruturas complementares entram quando o PCX e o pacote regional de regras demonstrarem sua necessidade. Consulte [Lógica do ESD e do Canal Escoadouro](LOGICA_ESD_E_CANAL_ESCOADOURO.md).

Depois das restrições duras, otimizar:

- Área plantável.
- Comprimento médio das linhas.
- Quantidade de manobras.
- Linhas curtas, bicos e falhas de paralelismo.
- Distância operacional.
- Corte e aterro.
- Custo de implantação e manutenção.

O resultado deve ser uma frente de Pareto com três a oito alternativas, e não uma única “solução ótima” escondida atrás de pesos arbitrários.

### 9.3 POA e logística de colheita

O POA é modelado como pátio/polígono: acessos, baias, fila, giro, superfície, drenagem e capacidade. Cada cenário de sulcação gera massa acumulada por tiro, `load_events` e `swap_windows`; estes alimentam um grafo dirigido até o POA e o destino rodoviário.

O motor combina localização capacitada, atribuição, roteamento e simulação de filas. Ele mede quilômetros vazio/carregado por tonelada, espera da colhedora, utilização de transbordos/baias/caminhões, entrega horária, tráfego sobre o canavial e robustez a produtividade, chuva e falhas.

O cálculo é iterativo: um POA ou uma saída logística pode alterar direção/fase dos sulcos e carreadores, mas toda mudança volta ao modelo de água e de tráfego. A especificação completa está em [POA e logística integrada da colheita](./POA_E_LOGISTICA_DE_COLHEITA.md).

## 10. Mapa de funcionalidades

### Fundação — P0

- Organizações, fazendas, projetos, usuários e permissões.
- Upload grande retomável, importação por URL e histórico imutável.
- Validação de formato, CRS, datum, sobreposição, cobertura e segurança.
- Catálogo de datasets, versões, metadados e linhagem.
- Contrato de pedido, catálogo de parâmetros, proveniência, overrides e invalidação.
- Catálogo geral de interferências `COMPLETE/PARTIAL/NOT_REVIEWED` e inventário elétrico `PROVIDED/DECLARED_NONE/NOT_REVIEWED`.
- Visualizador 2D, perfis e comparação de camadas.
- Central de qualidade e prontidão.
- Cadastro de solo, chuva, manejo, estruturas e máquinas.
- Jobs reais, progresso, falha, retomada e custo.

### Diagnóstico — P0

- Nuvem/MDT, declividade, curvas, microbacias e fluxo.
- Editor de bueiros, breaklines, barreiras e depressões.
- Entradas/saídas e área externa contribuinte.
- RUSLE com incerteza.
- Chuva de projeto simplificada e balanço de água.
- Achados georreferenciados e pendências.
- Relatório de diagnóstico reproduzível.

### Projeto — P1

- PCE e PCX digitais.
- Gerador de terraços, canais e saídas.
- Editor de blocos, carreadores e cabeceiras.
- Linhas-mestras e offsets de sulcação.
- Quebras de trabalho, hidráulica e rota por barreiras.
- Massa por tiro, eventos de enchimento e janelas seguras de troca.
- POAs como pátios capacitados, rotas vazias/carregadas, filas e tráfego.
- Verificador contínuo de regras.
- Cenários Pareto e comparador antes/depois.
- Perfis, volumes, custo e memória de cálculo.
- Revisão, comentários, exceções justificadas e aprovação.

### Campo e integração — P1/P2

- Aplicativo offline de vistoria e locação.
- Fotos, formulários, GNSS e pendências georreferenciadas.
- Pacotes por máquina/controlador e validação de compatibilidade.
- GeoPackage, DXF, SHP, KML/KMZ, CSV, PDF e adaptadores OEM.
- As built e comparação projeto/executado.
- Inspeção e manutenção pós-chuva.
- APIs, webhooks, integrações com FMIS e portfólio de fazendas.

### Futuro — P2

- Processamento de fotos brutas por provedor externo.
- Hidráulica 2D detalhada em hotspots.
- Despacho dinâmico/telemetria de CTT e travessias elétricas 3D especiais.
- Monitoramento por eventos e recalibração.
- Detecção assistida por IA de feições e anomalias.

## 11. Estados e bloqueios

### Dataset

`ausente → enviando → extraindo → validando → ação necessária → válido com ressalvas → aprovado → substituído`

### Projeto

`rascunho → diagnóstico pronto → alternativas → edição → verificações falharam/passaram → revisão → campo → aprovação RT → liberado → executado → substituído`

Qualquer alteração em MDT, solo, chuva, estruturas ou restrições invalida os resultados dependentes. Uma versão aprovada não pode mudar; uma correção cria nova revisão.

Bloqueadores executivos mínimos:

- CRS, unidade ou datum vertical desconhecido.
- Precisão vertical não demonstrada.
- Microbacia cortada ou contribuição externa não resolvida.
- Saída não identificada/protegida.
- Solo ou infiltração insuficiente para a prática proposta.
- IDF/TR sem fonte e justificativa.
- Estruturas ou APPs não conferidas.
- Violação de máquina ou geometria.
- Ausência de vistoria/responsável quando exigidos.

## 12. Saídas do produto

Cada pacote deve conter:

- Identificação do projeto e versão.
- Hash dos insumos e versão do algoritmo.
- Mapa de qualidade do MDT.
- Premissas medidas, informadas e inferidas.
- Diagnóstico PCE e PCX.
- Cenários rejeitados e motivos.
- Geometrias aprovadas.
- Perfis e memória de cálculo.
- Matriz de verificações.
- Riscos residuais e plano de manutenção.
- Aprovações e registro de responsabilidade.
- Arquivos de intercâmbio identificados por CRS/datum/unidade.

Exportações preliminares devem conter marca visual e atributo `status=PRELIMINAR`. A exportação para máquina fica indisponível enquanto houver bloqueador.

## 13. Conformidade e responsabilidade

- **Responsabilidade profissional:** validar atribuições efetivas no CREA. Agronomia, topografia/aerofotogrametria, georreferenciamento e obras especiais podem exigir profissionais ou registros diferentes.
- **ART:** quando a empresa ou profissional prestar e emitir projeto técnico, formalizar responsabilidade conforme a Lei 6.496/1977.
- **Ambiental:** tratar APPs, nascentes, cursos, áreas úmidas e vegetação protegida como restrições; verificar Código Florestal e normas estaduais/municipais. [Lei 12.651/2012](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12651.htm)
- **Água e terceiros:** descarga, barramento, intervenção em curso e condução para imóvel vizinho podem exigir autorização, outorga/licença e anuência específica.
- **Voo:** se houver serviço de aquisição, cumprir ANAC, ANATEL, Ministério da Defesa quando aplicável e autorização de acesso ao espaço aéreo pelo DECEA/SARPAS.
- **Dados:** limites, produtividade e infraestrutura rural são informações comerciais sensíveis e podem estar vinculadas a pessoas; aplicar LGPD, segregação de tenant, retenção e exclusão contratual.
- **Máquinas:** validar formato, CRS, unidade, versão do display e firmware; ISOXML ou “shapefile compatível” não garante que todo controlador interprete a linha da mesma forma.

O sistema deve manter uma matriz de requisitos por estado e tipo de intervenção. Ela orienta o profissional, mas não substitui parecer jurídico/ambiental local.

## 14. Limites e não objetivos

O TerraFlux não deve:

- Inferir relevo a partir de ortomosaico isolado.
- Tratar copa da cana como terreno.
- Inventar infiltração, horizonte B ou saída hidráulica.
- Usar RUSLE como simulador de enchente.
- Garantir ausência de erosão.
- Apagar depressões sem revisão.
- Projetar drenagem subterrânea apenas com imagem aérea.
- Substituir vistoria, atribuição profissional ou licenciamento.
- Liberar geometrias proprietárias sem validar controlador e firmware.
- Construir fotogrametria, solver hidráulico ou formato LAS do zero.

IA pode classificar objetos, sugerir feições e priorizar revisão. A decisão crítica continua baseada em GIS/hidrologia determinística, regras versionadas e responsável técnico.

## 15. Riscos centrais

| Risco | Efeito | Controle |
|---|---|---|
| Nuvem mede vegetação, não solo | Fluxo e cotas errados | Gate Ground%, época de voo, perfis e checkpoints |
| Datum vertical incorreto | Declives e locação inconsistentes | Metadado obrigatório e transformação registrada |
| Talhão corta bacia | Vazão subestimada | Contexto externo e bloqueio de borda |
| Estrada sem bueiro no MDT | Barragem digital falsa | Editor de conectividade e vistoria |
| Depressão real preenchida | Alagamento ocultado | Três versões de MDT e aprovação da correção |
| Solo inferido grosseiramente | Infiltração errada | Faixa de incerteza e ensaio obrigatório no executivo |
| IDF distante/desatualizada | Seção subdimensionada | Biblioteca rastreável e decisão do RT |
| Otimização operacional domina | Erosão e descarga perigosa | Água como restrição eliminatória |
| Exportação incompatível | Erro na máquina | Adaptador testado por modelo/firmware |
| Usuário confunde preliminar com executivo | Risco técnico/jurídico | Estados, marcas, bloqueio e auditoria |

## 16. Critérios de sucesso

North star: **hectares implantados e aprovados sem redesenho crítico**.

Guardrails:

- Zero exportação executiva com bloqueador não reconhecido.
- Zero resultado crítico sem fonte, versão e parâmetros.
- Cem por cento das alterações relevantes invalidam resultados dependentes.
- Cem por cento das linhas exportadas passam nas restrições duras.

Métricas:

- Taxa de upload aprovado.
- Tempo até diagnóstico válido.
- Minutos técnicos por hectare.
- Concordância entre especialistas.
- Recall de pontos críticos observados em campo.
- Violações por 100 km de linha.
- Diferença projeto versus as built.
- Retrabalho antes e depois da plataforma.
- Área útil, manobras, custo e risco residual.
- Conversão de piloto, repetição e margem por projeto.

## 17. Decisões pendentes para o piloto

1. Estado/região inicial e pacote técnico de referência.
2. Tipos de solo e faixas de declividade prioritários.
3. Padrão de aquisição e precisão vertical exigida por nível.
4. Modelos de máquinas/controladores inicialmente suportados.
5. Método de escoamento adotado no MVP e seus limites.
6. Critério regional de tolerância de perda e fatores RUSLE.
7. Fluxo de ART, revisão e assinatura.
8. Política de retenção e propriedade dos dados.
9. Modelo comercial: por hectare, projeto, organização ou processamento.
10. Cinco áreas que formarão o conjunto ouro.
11. Convenção de levantamento do `power_line_axis` e rule pack regional das faixas de exclusão.
12. Fazendas-piloto com produtividade, telemetria CTT, POAs e tempos de fila observados.

## 18. Produto comercial

### Posicionamento

“Plataforma de diagnóstico e projeto conservacionista assistido para canaviais” é defensável. “IA que entrega projeto executivo só com uma imagem” não é.

O mercado adjacente é fragmentado entre:

- SIG/CAD genérico, que oferece ferramentas mas não uma jornada agronômica.
- Fotogrametria e fornecedores de levantamento.
- Calculadoras de terraço e hidráulica.
- Soluções de movimentação de terra/land forming.
- Restituição de linhas e orientação de máquinas.
- FMIS e gestão de operações.

O TerraFlux deve integrar essas etapas sem tentar substituir todos os motores especializados.

### Pacotes sugeridos

| Pacote | Cliente | Entrega |
|---|---|---|
| Diagnose | Consultorias e usinas | QA, MDT, água, erosão e relatório preliminar |
| Design | Equipe técnica | Editores, cenários, PCE/PCX e sulcação assistida |
| Enterprise | Grupos agrícolas | Portfólio, APIs, rule packs, campo, SLA e integrações |
| Serviço técnico parceiro | Cliente sem equipe | Projeto revisado por profissional/empresa habilitada |

No início, operar como **software + serviço de implantação**, porque os dados chegam heterogêneos e as regras precisam ser calibradas. Autosserviço pleno só depois de a central de qualidade resolver a maioria dos casos sem intervenção.

### Hipótese de cobrança

- Assinatura por organização/usuários para colaboração e histórico.
- Créditos por hectare processado, diferenciados por nuvem, resolução e simulação.
- Implantação e pacote regional cobrados separadamente.
- Serviço profissional e ART fora da licença SaaS, por parceiro ou contrato específico.
- Storage adicional e retenção longa cobrados por volume.

Evitar preço exclusivamente por arquivo ou exportação: incentiva uploads fragmentados e dificulta prever compute. Medir margem por projeto usando bytes, pontos, células, CPU, revisão e suporte.

### Prova de valor

O caso comercial precisa comparar:

- Horas de especialista antes/depois.
- Retrabalho de campo.
- Área plantável.
- Manobras e distância operacional.
- Quilômetros de transbordo vazio/carregado, espera da colhedora e regularidade de entrega.
- Área trafegada, passadas repetidas e locais/quantidade de POAs.
- Terra movimentada.
- Estruturas super/subdimensionadas evitadas.
- Erosão, assoreamento e manutenção observados.
- Tempo entre levantamento e projeto aprovado.

## 19. Referências essenciais

- [IAC — Boletim Técnico 216: conservação do solo e da água em cana](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf)
- [Embrapa — manejo e conservação na cana-de-açúcar](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/correcao-e-adubacao/manejo-e-conservacao)
- [Embrapa — princípios de terraceamento](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/arroz/producao/sistema-de-cultivo/arroz-de-terras-altas/terraceamento)
- [ANA — Manuais do Programa Produtor de Água](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais)
- [SGB — Atlas Pluviométrico e estudos de chuvas intensas](https://www.sgb.gov.br/publicacoes-atlas-pluviometrico-e-estudos-de-chuvas-intensas)
- [Incra — Manual Técnico para Georreferenciamento de Imóveis Rurais](https://www.gov.br/incra/pt-br/assuntos/governanca-fundiaria/Manual_Tecnico_de_Georreferenciamento_2_Edicao.pdf/%40%40display-file/file)
- [IBGE — conversão de altitudes hgeoHNOR2020](https://www.ibge.gov.br/geociencias/modelos-digitais-de-superficie/modelos-digitais-de-superficie/31283-hgeohnor2020-modeloconversaoaltitudesgeometricasgnss-datumverticalsgb.html)
- [ASPRS — padrão de exatidão posicional 2024](https://old.asprs.org/archives/asprs-approves-edition-2-version-2-of-the-asprs-positional-accuracy-standards-for-digital-geospatial-data-2024.html)
- [Lei 6.496/1977 — ART](https://planalto.gov.br/ccivil_03/leis/l6496.htm)
- [DECEA — regras atuais para drones e SARPAS](https://www.decea.mil.br/drone/)
- [MTE — NR-31, segurança e saúde no trabalho rural](https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/participacao-social/conselhos-e-orgaos-colegiados/comissao-tripartite-partitaria-permanente/normas-regulamentadora/normas-regulamentadoras-vigentes/norma-regulamentadora-no-31-nr-31)
- [AgroAbdo — LOC, otimização de pátios e colheita](https://agroabdo.com.br/loc)
- [UFPR — otimização do deslocamento de tratores transbordos](https://acervodigital.ufpr.br/xmlui/handle/1884/98218)
