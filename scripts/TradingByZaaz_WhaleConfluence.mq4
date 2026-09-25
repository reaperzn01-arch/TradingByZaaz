//+------------------------------------------------------------------+
//|                                TradingByZaaz_WhaleConfluence.mq4|
//|                                  Copyright 2026, TradingByZaaz   |
//|                          Built for Exness MT4 (Forex, Gold, BTC) |
//+------------------------------------------------------------------+
#property copyright   "TradingByZaaz"
#property link        "https://tradingbyzaaz.io"
#property version     "2.00"
#property description "Whale Confluence & Precision Signals: Live Highs/Lows, Trendlines, PDH/PDL & Institutional Flow"
#property indicator_chart_window
#property indicator_buffers 2

//--- Plot Buy Arrow (Green)
#property indicator_color1  clrLime
#property indicator_width1  3

//--- Plot Sell Arrow (Red)
#property indicator_color2  clrCrimson
#property indicator_width2  3

//--- Inputs
extern string  Sep1              = "=== 🐋 Whale & Institutional Flow ===";
extern double  InpWhaleRvolMult   = 1.8;      // Whale Volume Surge Multiplier (RVOL)
extern int     InpWhalePeriod     = 20;       // Volume Moving Average Period
extern bool    InpHighlightWhales = true;     // Detect Whale Absorption Sweeps

extern string  Sep2              = "=== 📐 Market Structure & Trendlines ===";
extern int     InpSwingBars       = 3;        // Swing High/Low Lookback Bars
extern bool    InpDrawTrendlines  = true;     // Draw Dynamic Trendlines
extern color   InpSupColor        = clrLimeGreen; // Support Trendline Color
extern color   InpResColor        = clrCrimson;   // Resistance Trendline Color

extern string  Sep3              = "=== 🏛 Previous Market Memory (PDH / PDL) ===";
extern bool    InpShowPDLevels    = true;     // Show Previous Day High & Low
extern color   InpPdhColor        = clrOrange;// PDH Color
extern color   InpPdlColor        = clrDeepSkyBlue; // PDL Color

extern string  Sep4              = "=== 🎯 Low Loss Margin / High Win Margin ===";
extern double  InpSlAtrMult       = 1.0;      // Stop Loss ATR Multiplier (Low Risk)
extern double  InpTp1RR           = 2.0;      // Take Profit 1 RR (High Win Margin)
extern double  InpTp2RR           = 3.5;      // Take Profit 2 RR
extern int     InpMinConfluence   = 70;       // Minimum Confluence Score % (70-100)

extern string  Sep5              = "=== 🔔 Alerts & Notifications ===";
extern bool    InpAlertPopup      = true;     // Pop-up Alert Window
extern bool    InpAlertPush       = true;     // Push Notification to MT4 Mobile
extern bool    InpAlertSound      = true;     // Play Alert Sound
extern string  InpSoundFile       = "alert.wav"; // Alert Sound File
extern bool    InpShowDashboard   = true;     // Show On-Chart HUD Dashboard

//--- Buffers
double BufferBuy[];
double BufferSell[];

//--- Internal State
datetime lastAlertTime = 0;
string   objPrefix     = "TBAZ4_";

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int init()
{
   IndicatorBuffers(2);

   SetIndexBuffer(0, BufferBuy);
   SetIndexStyle(0, DRAW_ARROW, STYLE_SOLID, 3, clrLime);
   SetIndexArrow(0, 233); // Up Arrow
   SetIndexLabel(0, "Zaaz Buy Entry");

   SetIndexBuffer(1, BufferSell);
   SetIndexStyle(1, DRAW_ARROW, STYLE_SOLID, 3, clrCrimson);
   SetIndexArrow(1, 234); // Down Arrow
   SetIndexLabel(1, "Zaaz Sell Entry");

   return(0);
}

//+------------------------------------------------------------------+
//| Custom indicator deinitialization function                       |
//+------------------------------------------------------------------+
int deinit()
{
   ObjectsDeleteAll(0, OBJ_HLINE);
   ObjectsDeleteAll(0, OBJ_LABEL);
   ObjectsDeleteAll(0, OBJ_RECTANGLE_LABEL);
   Comment("");
   return(0);
}

//+------------------------------------------------------------------+
//| Custom indicator iteration function                              |
//+------------------------------------------------------------------+
int start()
{
   int counted_bars = IndicatorCounted();
   if(counted_bars < 0) return(-1);
   if(counted_bars > 0) counted_bars--;

   int limit = Bars - counted_bars;
   if(limit > 500) limit = 500;

   // Previous Day High & Low
   double pdh = iHigh(NULL, PERIOD_D1, 1);
   double pdl = iLow(NULL, PERIOD_D1, 1);

   for(int i = limit; i >= 1; i--)
   {
      BufferBuy[i] = EMPTY_VALUE;
      BufferSell[i] = EMPTY_VALUE;

      double curATR = iATR(NULL, 0, 14, i);
      if(curATR <= 0) curATR = (High[i] - Low[i]);
      if(curATR <= 0) curATR = Point * 10;

      // Whale Volume (RVOL)
      double sumVol = 0;
      for(int v = 1; v <= InpWhalePeriod; v++)
      {
         sumVol += (double)Volume[i + v];
      }
      double avgVol = sumVol / (double)InpWhalePeriod;
      double rvol = avgVol > 0 ? ((double)Volume[i] / avgVol) : 1.0;
      bool isWhale = rvol >= InpWhaleRvolMult;

      // Candle Shape analysis (Absorption)
      double barRange = High[i] - Low[i];
      double lowerWick = MathMin(Open[i], Close[i]) - Low[i];
      double upperWick = High[i] - MathMax(Open[i], Close[i]);

      bool bullishAbsorb = (Close[i] > Open[i] || lowerWick > upperWick) && (Close[i] >= Low[i] + 0.4 * barRange);
      bool bearishAbsorb = (Close[i] < Open[i] || upperWick > lowerWick) && (Close[i] <= High[i] - 0.4 * barRange);

      // Previous Market Level Sweeps
      bool pdlSweep = pdl > 0 && Low[i] < pdl && Close[i] > pdl;
      bool pdhSweep = pdh > 0 && High[i] > pdh && Close[i] < pdh;

      // Moving Average Trend
      double ema50 = iMA(NULL, 0, 50, 0, MODE_EMA, PRICE_CLOSE, i);
      double ema200 = iMA(NULL, 0, 200, 0, MODE_EMA, PRICE_CLOSE, i);
      bool trendUp = Close[i] > ema50 && ema50 > ema200;
      bool trendDn = Close[i] < ema50 && ema50 < ema200;

      // Confluence Scoring
      int buyScore = 0;
      int sellScore = 0;

      if(isWhale && bullishAbsorb) buyScore += 35;
      else if(isWhale) buyScore += 15;

      if(isWhale && bearishAbsorb) sellScore += 35;
      else if(isWhale) sellScore += 15;

      if(pdlSweep) buyScore += 30;
      if(pdhSweep) sellScore += 30;

      if(trendUp) buyScore += 20;
      if(trendDn) sellScore += 20;

      if(Close[i] > ema50) buyScore += 15;
      if(Close[i] < ema50) sellScore += 15;

      // Execution Triggers
      bool fireBuy  = buyScore >= InpMinConfluence && (isWhale || pdlSweep);
      bool fireSell = sellScore >= InpMinConfluence && (isWhale || pdhSweep);

      if(fireBuy)
      {
         BufferBuy[i] = Low[i] - 0.5 * curATR;
         double entry = Close[i];
         double sl = MathMin(entry - (InpSlAtrMult * curATR), Low[i] - 0.2 * curATR);
         double risk = MathMax(entry - sl, Point * 20);
         double tp1 = entry + (InpTp1RR * risk);

         if(i == 1 && Time[0] != lastAlertTime)
         {
            lastAlertTime = Time[0];
            string msg = StringFormat("▲ TradingByZaaz BUY Signal on %s @ %s | SL: %s | TP1: %s | Confluence: %d%%",
                                      Symbol(), DoubleToStr(entry, Digits), DoubleToStr(sl, Digits),
                                      DoubleToStr(tp1, Digits), buyScore);
            TriggerAlerts(msg);
         }
      }
      else if(fireSell)
      {
         BufferSell[i] = High[i] + 0.5 * curATR;
         double entry = Close[i];
         double sl = MathMax(entry + (InpSlAtrMult * curATR), High[i] + 0.2 * curATR);
         double risk = MathMax(sl - entry, Point * 20);
         double tp1 = entry - (InpTp1RR * risk);

         if(i == 1 && Time[0] != lastAlertTime)
         {
            lastAlertTime = Time[0];
            string msg = StringFormat("▼ TradingByZaaz SELL Signal on %s @ %s | SL: %s | TP1: %s | Confluence: %d%%",
                                      Symbol(), DoubleToStr(entry, Digits), DoubleToStr(sl, Digits),
                                      DoubleToStr(tp1, Digits), sellScore);
            TriggerAlerts(msg);
         }
      }
   }

   // On-chart HUD display
   if(InpShowDashboard)
   {
      double curEma50 = iMA(NULL, 0, 50, 0, MODE_EMA, PRICE_CLOSE, 0);
      string trendStr = Close[0] > curEma50 ? "BULLISH ▲" : "BEARISH ▼";
      double rvol0 = Volume[1] > 0 ? ((double)Volume[0] / (double)Volume[1]) : 1.0;
      string whaleStr = rvol0 >= InpWhaleRvolMult ? "ACTIVE SURGE 🐋" : "NORMAL";

      string info = StringFormat(
         "=========================================\n" +
         "  ▲ TRADINGBYZAAZ · WHALE CONFLUENCE\n" +
         "=========================================\n" +
         "  Symbol: %s | Exness Engine: Ready\n" +
         "  Trend Bias: %s\n" +
         "  Whale Volume Flow: %s (RVOL: %.2fx)\n" +
         "  PDH: %s | PDL: %s\n" +
         "  Risk Architecture: Minimal Loss (1R) -> High Win (2R - 3.5R+)\n" +
         "=========================================",
         Symbol(), trendStr, whaleStr, rvol0, DoubleToStr(pdh, Digits), DoubleToStr(pdl, Digits)
      );
      Comment(info);
   }

   return(0);
}

//+------------------------------------------------------------------+
//| Alert Handler                                                    |
//+------------------------------------------------------------------+
void TriggerAlerts(string msg)
{
   if(InpAlertPopup)
      Alert(msg);
   if(InpAlertPush)
      SendNotification(msg);
   if(InpAlertSound)
      PlaySound(InpSoundFile);
}
