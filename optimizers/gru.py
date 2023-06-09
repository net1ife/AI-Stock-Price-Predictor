import streamlit as st
import numpy as np
import pandas as pd
import yfinance as yf
from ta.volatility import BollingerBands
from ta.trend import MACD
from ta.momentum import RSIIndicator
from ta.volume import VolumeWeightedAveragePrice
from ta.others import DailyReturnIndicator
from ta.utils import dropna
from sklearn.preprocessing import MinMaxScaler
import torch
import torch.nn as nn
import torch.optim as optim
from datetime import datetime, timedelta
from ta.trend import SMAIndicator, EMAIndicator
from ta.momentum import StochasticOscillator
from ta.volatility import AverageTrueRange
from ta.trend import CCIIndicator
from ta.volume import OnBalanceVolumeIndicator
from sklearn.model_selection import ParameterGrid
import copy

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class GRUModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim, dropout_rate):
        super(GRUModel, self).__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True, dropout=dropout_rate)
        self.fc = nn.Linear(hidden_dim, output_dim)
        self.dropout = nn.Dropout(dropout_rate)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_dim).to(device)
        out, _ = self.gru(x, h0)
        out = self.dropout(out)
        out = self.fc(out[:, -1, :])
        return out

def train_model(model, X, y, num_epochs, criterion, optimizer):
    model.train()
    progress_bar = st.progress(0)
    loss_values = []

    for t in range(num_epochs):
        optimizer.zero_grad()
        outputs = model(X)
        loss = criterion(outputs, y)
        loss.backward()
        optimizer.step()

        if t % 10 == 0:
            loss_values.append(loss.item())
            print(f"Epoch {t} train loss: {loss.item()}")

        progress_bar.progress((t + 1) / num_epochs)

    model.eval()
    training_loss_chart = st.line_chart(pd.DataFrame(loss_values, columns=['loss']))
    return model, training_loss_chart

def fetch_historical_data(ticker, start_date, end_date):
    data = yf.download(ticker, start=start_date, end=end_date, progress=False)
    data = dropna(data)
    return data

def add_selected_ta_features(data):
    # Bollinger Bands
    indicator_bb = BollingerBands(close=data["Close"], window=20, window_dev=2)
    data['bb_bbm'] = indicator_bb.bollinger_mavg()
    data['bb_bbh'] = indicator_bb.bollinger_hband()
    data['bb_bbl'] = indicator_bb.bollinger_lband()

    # Moving Average Convergence Divergence (MACD)
    indicator_macd = MACD(close=data["Close"], window_slow=26, window_fast=12, window_sign=9)
    data['macd'] = indicator_macd.macd()
    data['macd_signal'] = indicator_macd.macd_signal()
    data['macd_diff'] = indicator_macd.macd_diff()

    # Relative Strength Index (RSI)
    indicator_rsi = RSIIndicator(close=data["Close"], window=14)
    data['rsi'] = indicator_rsi.rsi()

    # Volume Weighted Average Price (VWAP)
    indicator_vwap = VolumeWeightedAveragePrice(high=data['High'], low=data['Low'], close=data['Close'], volume=data['Volume'])
    data['vwap'] = indicator_vwap.volume_weighted_average_price()

    # Daily Return
    indicator_daily_return = DailyReturnIndicator(close=data['Close'])
    data['daily_return'] = indicator_daily_return.daily_return()

    # Simple Moving Average (SMA)
    indicator_sma = SMAIndicator(close=data["Close"], window=14)
    data['sma'] = indicator_sma.sma_indicator()

    # Exponential Moving Average (EMA)
    indicator_ema = EMAIndicator(close=data["Close"], window=14)
    data['ema'] = indicator_ema.ema_indicator()

    # Stochastic Oscillator
    indicator_so = StochasticOscillator(high=data['High'], low=data['Low'], close=data['Close'], window=14)
    data['so_k'] = indicator_so.stoch()
    data['so_d'] = indicator_so.stoch_signal()

    # Average True Range (ATR)
    indicator_atr = AverageTrueRange(high=data['High'], low=data['Low'], close=data['Close'], window=14)
    data['atr'] = indicator_atr.average_true_range()

    # Commodity Channel Index (CCI)
    indicator_cci = CCIIndicator(high=data['High'], low=data['Low'], close=data['Close'], window=20)
    data['cci'] = indicator_cci.cci()

    # On Balance Volume (OBV)
    indicator_obv = OnBalanceVolumeIndicator(close=data['Close'], volume=data['Volume'])
    data['obv'] = indicator_obv.on_balance_volume()

    return data

def normalize_data(data):
    scaler = MinMaxScaler(feature_range=(-1, 1))
    data_normalized = scaler.fit_transform(data)
    return data_normalized, scaler

def create_sequences(data, seq_length):
    xs = []
    ys = []

    for i in range(len(data) - seq_length - 1):
        x = data[i:(i + seq_length)]
        y = data[i + seq_length]
        xs.append(x)
        ys.append(y)

    return np.array(xs), np.array(ys)

# Sidebar input
hidden_dim = st.sidebar.slider("Hidden Dimension", min_value=1, max_value=100, value=64)
num_layers = st.sidebar.slider("Number of Layers", min_value=1, max_value=5, value=3)
num_epochs = int(st.sidebar.text_input("Number of Epochs", value='150')) 
learning_rate = float(st.sidebar.text_input("Learning Rate", value='0.0005'))  
seq_length = st.sidebar.slider("Sequence Length", min_value=1, max_value=200, value=80)
dropout_rate = st.sidebar.slider("Dropout Rate", min_value=0.0, max_value=0.5, value=0.2, step=0.05)
ticker = st.sidebar.text_input("Ticker", "SPY")
num_cycles = int(st.sidebar.text_input("Number of Prediction Cycles", value='1'))  

start_date = st.sidebar.date_input("Start Date", datetime.now() - timedelta(days=365 * 5))
end_date = st.sidebar.date_input("End Date", datetime.now() - timedelta(days=1))

next_day_datetime = end_date + timedelta(days=1)
next_day = next_day_datetime.strftime("%Y-%m-%d")

best_model = None
best_predictions = None

param_grid = {
    'hidden_dim': [32, 64, 128],
    'num_layers': [2, 3, 4],
    'learning_rate': [0.001, 0.0005, 0.0001],
    'seq_length': [60, 80, 100],
    'dropout_rate': [0.1, 0.2, 0.3]
}

best_loss = float('inf')
best_params = None
best_model = None
best_predictions = None

# Grid search over the parameters
for params in ParameterGrid(param_grid):
    st.write(f"Training with parameters: {params}")

    hidden_dim = params['hidden_dim']
    num_layers = params['num_layers']
    learning_rate = params['learning_rate']
    seq_length = params['seq_length']
    dropout_rate = params['dropout_rate']

    for cycle in range(num_cycles):
    # Fetch data, preprocess, and create sequences
        data = fetch_historical_data(ticker, start_date, end_date)
        data = add_selected_ta_features(data)
        data = data.dropna()
        data, scaler = normalize_data(data.values)
        X, y = create_sequences(data, seq_length)

        X = torch.from_numpy(X).float().to(device)
        y = torch.from_numpy(y).float().to(device)

        input_dim = X.shape[2]
        output_dim = data.shape[1]

        model = GRUModel(input_dim, hidden_dim, num_layers, output_dim, dropout_rate).to(device)

        criterion = torch.nn.MSELoss(reduction='mean')
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

        model, training_loss_chart = train_model(model, X, y, num_epochs, criterion, optimizer)

        X_predict = X[-1:, :, :]
        outputs = model(X_predict)
        predictions = outputs.detach().cpu().numpy()
        predictions = scaler.inverse_transform(predictions)


        st.subheader(f"Predicted prices for {ticker} on {next_day}:")
        st.write(f"Open: {predictions[0, 0]}")
        st.write(f"High: {predictions[0, 1]}")
        st.write(f"Low: {predictions[0, 2]}")
        st.write(f"Close: {predictions[0, 3]}")

        # Fetch actual data for the predicted day
        actual_data = fetch_historical_data(ticker, next_day, next_day_datetime + timedelta(days=1))

        if not actual_data.empty:
            st.subheader(f"Actual prices for {ticker} on {next_day}:")
            st.write(f"Open: {actual_data['Open'].values[0]}")
            st.write(f"High: {actual_data['High'].values[0]}")
            st.write(f"Low: {actual_data['Low'].values[0]}")
            st.write(f"Close: {actual_data['Close'].values[0]}")

        loss = criterion(outputs, y[-len(predictions):])
        if best_predictions is None or np.all(predictions > best_predictions):
            best_model = model
            best_predictions = predictions
            best_loss = loss.item()
            best_params = params
            st.write(f"New best loss: {best_loss} with parameters: {best_params}")

        end_date = next_day_datetime
        next_day_datetime += timedelta(days=1)
        next_day = next_day_datetime.strftime("%Y-%m-%d")

        # Check if current model has the best loss
        if loss.item() < best_loss:
            best_loss = loss.item()
            best_params = params
            best_model = copy.deepcopy(model) 


st.write(f"Best parameters found: {best_params} with loss: {best_loss}")

if best_model is not None:
    model_path = f"best_model.pt"
    torch.save(best_model.state_dict(), model_path)

    st.subheader("Best Predictions:")
    st.write(f"Open: {best_predictions[0, 0]}")
    st.write(f"High: {best_predictions[0, 1]}")
    st.write(f"Low: {best_predictions[0, 2]}")
    st.write(f"Close: {best_predictions[0, 3]}")

    st.write(f"Best model saved to: {model_path}")

st.subheader("Prediction completed.")
