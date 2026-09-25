//+------------------------------------------------------------------+
//|                                TradingByZaaz_WhaleConfluence.mq5|
//|                                  Copyright 2026, TradingByZaaz   |
//|                          Built for Exness MT5 (Forex, Gold, BTC) |
//+------------------------------------------------------------------+
#property copyright   "TradingByZaaz"
#property link        "https://tradingbyzaaz.io"
#property version     "2.00"
#property description "Whale Confluence & Precision Signals: Live Highs/Lows, Trendlines, PDH/PDL & Institutional Flow"
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   2

//--- Plot Buy Arrow (Green)
#property indicator_label1  "Zaaz Buy Entry"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrLime
#property indicator_style1  STYLE_SOLID
#property indicator_width1  3

//--- Plot Sell Arrow (Red)
#property indicator_label2  "Zaaz Sell Entry"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrCrimson
#property indicator_style2  STYLE_SOLID
#property indicator_width2  3

//--- Inputs
input group "=== 🐋 Whale & Institutional Flow ==="
input double   InpWhaleRvolMult   = 1.8;      // Whale Volume Surge Multiplier (RVOL)
input int      InpWhalePeriod     = 20;       // Volume Moving Average Period
input bool     InpHighlightWhales = true;     // Detect Whale Absorption Sweeps

input group "=== 📐 Market Structure & Trendlines ==="
input int      InpSwingBars       = 3;        // Swing High/Low Lookback Bars
input bool     InpDrawTrendlines  = true;     // Draw Dynamic Trendlines
input color    InpSupColor        = clrLimeGreen; // Support Trendline Color
input color    InpResColor        = clrCrimson;   // Resistance Trendline Color

input group "=== 🏛 Previous Market Memory (PDH / PDL) ==="
input bool     InpShowPDLevels    = true;     // Show Previous Day High & Low
input color    InpPdhColor        = clrOrange;// PDH Color
input color    InpPdlColor        = clrDeepSkyBlue; // PDL Color

input group "=== 🎯 Low Loss Margin / High Win Margin ==="
input double   InpSlAtrMult       = 1.0;      // Stop Loss ATR Multiplier (Low Risk)
input double   InpTp1RR           = 2.0;      // Take Profit 1 RR (High Win Margin)
input double   InpTp2RR           = 3.5;      // Take Profit 2 RR
input int      InpMinConfluence   = 70;       // Minimum Confluence Score % (70-100)

input group "=== 🔔 Alerts & Notifications ==="
input bool     InpAlertPopup      = true;     // Pop-up Alert Window
input bool     InpAlertPush       = true;     // Push Notification to MT5 Mobile
input bool     InpAlertSound      = true;     // Play Alert Sound
input string   InpSoundFile       = "alert.wav"; // Alert Sound File
input bool     InpShowDashboard   = true;     // Show On-Chart HUD Dashboard

//--- Buffers
double BufferBuy[];
double BufferSell[];
double BufferSL[];
double BufferTP1[];

//--- Indicator Handles
int handleATR;
int handleEMA50;
int handleEMA200;

//--- Internal State
datetime lastAlertTime = 0;
string   objPrefix     = "TBAZ_";

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Bind Buffers
   SetIndexBuffer(0, BufferBuy, INDICATOR_DATA);
   SetIndexBuffer(1, BufferSell, INDICATOR_DATA);
   SetIndexBuffer(2, BufferSL, INDICATOR_CALCULATIONS);
   SetIndexBuffer(3, BufferTP1, INDICATOR_CALCULATIONS);

   PlotIndexSetInteger(0, PLOT_ARROW, 233); // Up Arrow
   PlotIndexSetInteger(1, PLOT_ARROW, 234); // Down Arrow

   ArraySetAsSeries(BufferBuy, true);
   ArraySetAsSeries(BufferSell, true);
   ArraySetAsSeries(BufferSL, true);
   ArraySetAsSeries(BufferTP1, true);

   //--- Indicators
   handleATR = iATR(_Symbol, _Period, 14);
   handleEMA50 = iMA(_Symbol, _Period, 50, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA200 = iMA(_Symbol, _Period, 200, 0, MODE_EMA, PRICE_CLOSE);

   if(handleATR == INVALID_HANDLE || handleEMA50 == INVALID_HANDLE || handleEMA200 == INVALID_HANDLE)
   {
      Print("Error creating indicator handles in TradingByZaaz");
      return INIT_FAILED;
   }

   //--- Initialize Dashboard
   if(InpShowDashboard)
      CreateDashboard();

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Custom indicator deinitialization function                       |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(handleATR);
   IndicatorRelease(handleEMA50);
   IndicatorRelease(handleEMA200);

   // Clean up graphical chart objects
   ObjectsDeleteAll(0, objPrefix);
   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Custom indicator iteration function                              |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < 100) return 0;

   int limit = rates_total - prev_calculated;
   if(limit <= 0) limit = 1;
   if(prev_calculated == 0) limit = MathMin(rates_total - 2, 500);

   // Set timeseries indexing
   ArraySetAsSeries(time, true);
   ArraySetAsSeries(open, true);
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);
   ArraySetAsSeries(tick_volume, true);

   // Copy indicators
   double atrVal[], ema50Val[], ema200Val[];
   ArraySetAsSeries(atrVal, true);
   ArraySetAsSeries(ema50Val, true);
   ArraySetAsSeries(ema200Val, true);

   CopyBuffer(handleATR, 0, 0, limit + 10, atrVal);
   CopyBuffer(handleEMA50, 0, 0, limit + 10, ema50Val);
   CopyBuffer(handleEMA200, 0, 0, limit + 10, ema200Val);

   // Fetch Previous Day High and Low (PDH/PDL)
   MqlRates dailyRates[];
   ArraySetAsSeries(dailyRates, true);
   double pdh = 0.0, pdl = 0.0;
   if(CopyRates(_Symbol, PERIOD_D1, 1, 1, dailyRates) > 0)
   {
      pdh = dailyRates[0].high;
      pdl = dailyRates[0].low;
   }

   // Loop over candles
   for(int i = limit; i >= 1; i--)
   {
      BufferBuy[i] = EMPTY_VALUE;
      BufferSell[i] = EMPTY_VALUE;

      double curATR = atrVal[i] > 0 ? atrVal[i] : (high[i] - low[i]);
      if(curATR <= 0) curATR = _Point * 10;

      // Calculate Whale Relative Volume (RVOL)
      double sumVol = 0;
      for(int v = 1; v <= InpWhalePeriod; v++)
      {
         sumVol += (double)tick_volume[i + v];
      }
      double avgVol = sumVol / (double)InpWhalePeriod;
      double rvol = avgVol > 0 ? ((double)tick_volume[i] / avgVol) : 1.0;
      bool isWhale = rvol >= InpWhaleRvolMult;

      // Candle Shape analysis (Absorption / Wick analysis)
      double barRange = high[i] - low[i];
      double lowerWick = MathMin(open[i], close[i]) - low[i];
      double upperWick = high[i] - MathMax(open[i], close[i]);

      bool bullishAbsorb = (close[i] > open[i] || lowerWick > upperWick) && (close[i] >= low[i] + 0.4 * barRange);
      bool bearishAbsorb = (close[i] < open[i] || upperWick > lowerWick) && (close[i] <= high[i] - 0.4 * barRange);

      // Previous Market Level Sweeps
      bool pdlSweep = pdl > 0 && low[i] < pdl && close[i] > pdl;
      bool pdhSweep = pdh > 0 && high[i] > pdh && close[i] < pdh;

      // Swing High/Low detection
      bool isSwingHigh = true, isSwingLow = true;
      for(int s = 1; s <= InpSwingBars; s++)
      {
         if(high[i] < high[i - s] || high[i] < high[i + s]) isSwingHigh = false;
         if(low[i] > low[i - s] || low[i] > low[i + s]) isSwingLow = false;
      }

      // Moving Average Trend
      bool trendUp = close[i] > ema50Val[i] && ema50Val[i] > ema200Val[i];
      bool trendDn = close[i] < ema50Val[i] && ema50Val[i] < ema200Val[i];

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

      if(close[i] > ema50Val[i]) buyScore += 15;
      if(close[i] < ema50Val[i]) sellScore += 15;

      // Execution Triggers
      bool fireBuy  = buyScore >= InpMinConfluence && (isWhale || pdlSweep);
      bool fireSell = sellScore >= InpMinConfluence && (isWhale || pdhSweep);

      if(fireBuy)
      {
         BufferBuy[i] = low[i] - 0.5 * curATR;
         double entry = close[i];
         double sl = MathMin(entry - (InpSlAtrMult * curATR), low[i] - 0.2 * curATR);
         double risk = MathMax(entry - sl, _Point * 20);
         double tp1 = entry + (InpTp1RR * risk);
         double tp2 = entry + (InpTp2RR * risk);

         BufferSL[i] = sl;
         BufferTP1[i] = tp1;

         // Trigger alert only on the latest closed candle
         if(i == 1 && time[0] != lastAlertTime)
         {
            lastAlertTime = time[0];
            string msg = StringFormat("▲ TradingByZaaz BUY Signal on %s @ %s | SL: %s | TP1: %s | Confluence: %d%%",
                                      _Symbol, DoubleToString(entry, _Digits), DoubleToString(sl, _Digits),
                                      DoubleToString(tp1, _Digits), buyScore);
            TriggerAlerts(msg);
         }
      }
      else if(fireSell)
      {
         BufferSell[i] = high[i] + 0.5 * curATR;
         double entry = close[i];
         double sl = MathMax(entry + (InpSlAtrMult * curATR), high[i] + 0.2 * curATR);
         double risk = MathMax(sl - entry, _Point * 20);
         double tp1 = entry - (InpTp1RR * risk);
         double tp2 = entry - (InpTp2RR * risk);

         BufferSL[i] = sl;
         BufferTP1[i] = tp1;

         if(i == 1 && time[0] != lastAlertTime)
         {
            lastAlertTime = time[0];
            string msg = StringFormat("▼ TradingByZaaz SELL Signal on %s @ %s | SL: %s | TP1: %s | Confluence: %d%%",
                                      _Symbol, DoubleToString(entry, _Digits), DoubleToString(sl, _Digits),
                                      DoubleToString(tp1, _Digits), sellScore);
            TriggerAlerts(msg);
         }
      }
   }

   // Update Chart Dashboard & Lines
   if(InpShowDashboard)
   {
      double curRvol = (double)tick_volume[0] / MathMax(1.0, (double)tick_volume[1]);
      bool isBull = close[0] > ema50Val[0];
      UpdateDashboard(close[0], pdh, pdl, isBull, curRvol);
   }

   // Draw PDH/PDL Lines on Chart
   if(InpShowPDLevels && pdh > 0 && pdl > 0)
   {
      DrawHLine(objPrefix + "PDH", pdh, InpPdhColor, "PDH (Previous Day High)");
      DrawHLine(objPrefix + "PDL", pdl, InpPdlColor, "PDL (Previous Day Low)");
   }

   return rates_total;
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

//+------------------------------------------------------------------+
//| Draw Horizontal Line Helper                                      |
//+------------------------------------------------------------------+
void DrawHLine(string name, double price, color col, string tooltip)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
      ObjectSetInteger(0, name, OBJPROP_COLOR, col);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);
   }
   else
   {
      ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   }
}

//+------------------------------------------------------------------+
//| Dashboard Graphical UI                                           |
//+------------------------------------------------------------------+
void CreateDashboard()
{
   string name = objPrefix + "DASH_BG";
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 20);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 20);
      ObjectSetInteger(0, name, OBJPROP_XSIZE, 240);
      ObjectSetInteger(0, name, OBJPROP_YSIZE, 155);
      ObjectSetInteger(0, name, OBJPROP_BGCOLOR, C'16,21,30');
      ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, C'45,55,75');
      ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   }
}

void UpdateDashboard(double price, double pdh, double pdl, bool trendUp, double rvol)
{
   CreateLabel(objPrefix + "L_TITLE", "▲ TradingByZaaz Whale Flow", 30, 30, clrDeepSkyBlue, 10, true);
   CreateLabel(objPrefix + "L_SYM", _Symbol + " (" + EnumToString((ENUM_TIMEFRAMES)_Period) + ")", 30, 52, clrWhite, 9, false);

   string trendStr = trendUp ? "Trend: BULLISH ▲" : "Trend: BEARISH ▼";
   color trendCol = trendUp ? clrLime : clrCrimson;
   CreateLabel(objPrefix + "L_TREND", trendStr, 30, 72, trendCol, 9, false);

   string whaleStr = rvol >= InpWhaleRvolMult ? "Whale Flow: SURGE 🐋" : "Whale Flow: NORMAL";
   color whaleCol = rvol >= InpWhaleRvolMult ? clrAqua : clrSilver;
   CreateLabel(objPrefix + "L_WHALE", whaleStr, 30, 92, whaleCol, 9, false);

   string pdStr = StringFormat("PDH: %s | PDL: %s", DoubleToString(pdh, 2), DoubleToString(pdl, 2));
   CreateLabel(objPrefix + "L_PD", pdStr, 30, 112, clrOrange, 8, false);

   CreateLabel(objPrefix + "L_RISK", "Risk: 1R | Win Margin: 2R-3.5R+", 30, 132, clrGold, 8, true);
}

void CreateLabel(string name, string text, int x, int y, color col, int fontSize, bool bold)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 240 - x);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
   ObjectSetString(0, name, OBJPROP_FONT, bold ? "Arial Bold" : "Arial");
}
