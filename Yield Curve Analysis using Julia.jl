import Pkg

Pkg.activate(temp=true)
Pkg.add([
    "CSV",
    "DataFrames",
    "Dates",
    "Statistics",
    "CairoMakie",
    "AlgebraOfGraphics",
    "CategoricalArrays"
])

using CSV
using DataFrames
using Dates
using Statistics
using CairoMakie
using AlgebraOfGraphics

# Render as SVG in notebook for sharp output
CairoMakie.activate!(type = "svg")

# Professional, clean theme (institutional / research-report style)
set_theme!(Theme(
    fontsize = 14,
    palette = (color = Makie.wong_colors(),),
    figure_padding = 12,
    Axis = (
        backgroundcolor = :white,
        xgridcolor = :gray90,
        ygridcolor = :gray90,
        xminorgridvisible = false,
        yminorgridvisible = false,
        topspinevisible = false,
        rightspinevisible = false,
        leftspinecolor = :gray40,
        bottomspinecolor = :gray40,
        titlefont = :bold,
        titlecolor = :black,
        xlabelcolor = :gray25,
        ylabelcolor = :gray25,
        xticklabelcolor = :gray25,
        yticklabelcolor = :gray25
    ),
    Legend = (
        framevisible = false,
        backgroundcolor = :transparent,
        labelsize = 12,
        titlesize = 12
    )
))

function save_both(fig, name::AbstractString)
    save("$(name).svg", fig)
    save("$(name).png", fig)
end

csv_path = "/content/yield_curve.csv"   # <-- change if needed

raw = DataFrame(CSV.File(
    csv_path;
    normalizenames = false,
    missingstring = ["", "NA", "N/A", "null", "NULL"]
))

# Clean whitespace/newlines in headers (important if file came from Excel)
clean_names = strip.(replace.(String.(names(raw)), r"\s+" => " "))
rename!(raw, Pair.(names(raw), clean_names))

# Validate expected columns
expected_cols = [
    "Date",
    "91 days T.Bill",
    "182 days T.Bill",
    "364 days T.Bill",
    "2yr T.Bond",
    "5yr T.Bond",
    "10yr T.Bond",
    "15yr T.Bond",
    "20yr T.Bond"
]

missing_cols = setdiff(expected_cols, String.(names(raw)))
@assert isempty(missing_cols) "Missing expected columns: $(missing_cols)"

# Rename to easier Julia-friendly symbols
rename!(raw, Dict(
    "Date" => :Date,
    "91 days T.Bill" => :TBill_91d,
    "182 days T.Bill" => :TBill_182d,
    "364 days T.Bill" => :TBill_364d,
    "2yr T.Bond" => :TBond_2y,
    "5yr T.Bond" => :TBond_5y,
    "10yr T.Bond" => :TBond_10y,
    "15yr T.Bond" => :TBond_15y,
    "20yr T.Bond" => :TBond_20y
))

# Parse Date safely
if !(eltype(raw.Date) <: Date)
    raw.Date = Date.(string.(raw.Date), dateformat"m/d/y")
end

# Force numeric columns to Float64 / missing
for c in names(raw, Not(:Date))
    raw[!, c] = passmissing(x -> x isa Number ? Float64(x) : parse(Float64, string(x))).(raw[!, c])
end

sort!(raw, :Date)

first(raw, 5)

tenor_map = DataFrame(
    Tenor = [
        :TBill_91d,
        :TBill_182d,
        :TBill_364d,
        :TBond_2y,
        :TBond_5y,
        :TBond_10y,
        :TBond_15y,
        :TBond_20y
    ],
    Label = [
        "91D", "182D", "364D", "2Y", "5Y", "10Y", "15Y", "20Y"
    ],
    Years = [0.25, 0.50, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0]
)

# Ensure we stack into Symbols to match tenor_map
curve_long = stack(raw, Not(:Date), variable_name = :Tenor, value_name = :Yield)
curve_long.Tenor = Symbol.(curve_long.Tenor)

leftjoin!(curve_long, tenor_map, on = :Tenor)
dropmissing!(curve_long, :Yield)
sort!(curve_long, [:Date, :Years])

display(first(curve_long, 10))

df = deepcopy(raw)

# Level = average of medium/long tenors
df.Level = [
    mean(skipmissing([
        r.TBond_2y,
        r.TBond_5y,
        r.TBond_10y,
        r.TBond_15y,
        r.TBond_20y
    ]))
    for r in eachrow(df)
]

# Classic slope measures
df.Slope_10y_2y = df.TBond_10y .- df.TBond_2y
df.Slope_20y_91d = df.TBond_20y .- df.TBill_91d

# Curvature proxy
df.Curvature_5y = 2 .* df.TBond_5y .- df.TBond_2y .- df.TBond_10y

# Build long-form factors table
factors = stack(
    select(df, :Date, :Level, :Slope_10y_2y, :Slope_20y_91d, :Curvature_5y),
    Not(:Date),
    variable_name = :Metric,
    value_name = :Value
)

# Map metrics using Strings as stack produces Strings by default
metric_labels = Dict(
    "Level" => "Level (Avg 2Y/5Y/10Y/15Y/20Y)",
    "Slope_10y_2y" => "Slope (10Y - 2Y)",
    "Slope_20y_91d" => "Slope (20Y - 91D)",
    "Curvature_5y" => "Curvature (2×5Y - 2Y - 10Y)"
)

factors.Label = [metric_labels[m] for m in factors.Metric]
display(first(factors, 5))

using CategoricalArrays

function latest_on_or_before(dates::Vector{Date}, target::Date)
    idx = searchsortedlast(dates, target)
    return idx == 0 ? first(dates) : dates[idx]
end

all_dates = sort(unique(df.Date))
latest_date = last(all_dates)

anchor_dates = [
    ("Latest", latest_date),
    ("1M Ago", latest_on_or_before(all_dates, latest_date - Month(1))),
    ("6M Ago", latest_on_or_before(all_dates, latest_date - Month(6))),
    ("1Y Ago", latest_on_or_before(all_dates, latest_date - Year(1))),
    ("5Y Ago", latest_on_or_before(all_dates, latest_date - Year(5)))
]

snapshot_frames = DataFrame[]
for (label, d) in anchor_dates
    row = df[df.Date .== d, :]
    tmp = stack(select(row, Not(:Date)), variable_name = :Tenor, value_name = :Yield)
    tmp.Tenor = Symbol.(tmp.Tenor)
    leftjoin!(tmp, tenor_map, on = :Tenor)
    tmp.AsOf = fill("$(label) — $(Dates.format(d, dateformat"yyyy-mm-dd"))", nrow(tmp))
    push!(snapshot_frames, tmp)
end

snapshots = vcat(snapshot_frames...)
dropmissing!(snapshots, [:Yield, :Label])

# Force correct categorical ordering for the X-axis
snapshots.Label = categorical(snapshots.Label, levels=tenor_map.Label, ordered=true)

sort!(snapshots, [:AsOf, :Years])

# Reference dates
d_1m = latest_on_or_before(all_dates, latest_date - Month(1))
d_6m = latest_on_or_before(all_dates, latest_date - Month(6))
d_1y = latest_on_or_before(all_dates, latest_date - Year(1))

current_row = df[df.Date .== latest_date, :]
row_1m     = df[df.Date .== d_1m, :]
row_6m     = df[df.Date .== d_6m, :]
row_1y     = df[df.Date .== d_1y, :]

tenor_cols = tenor_map.Tenor

change_df = DataFrame(
    Tenor = tenor_cols,
    Label = tenor_map.Label,
    Years = tenor_map.Years,
    Change_1M_bp = [100 * (current_row[1, c] - row_1m[1, c]) for c in tenor_cols],
    Change_6M_bp = [100 * (current_row[1, c] - row_6m[1, c]) for c in tenor_cols],
    Change_1Y_bp = [100 * (current_row[1, c] - row_1y[1, c]) for c in tenor_cols]
)

delta_long = stack(
    change_df,
    [:Change_1M_bp, :Change_6M_bp, :Change_1Y_bp],
    variable_name = :Horizon,
    value_name = :ChangeBP
)

# Map metrics using Strings as stack produces Strings by default
horizon_labels = Dict(
    "Change_1M_bp" => "1M \u0394",
    "Change_6M_bp" => "6M \u0394",
    "Change_1Y_bp" => "1Y \u0394"
)

delta_long.HorizonLabel = [horizon_labels[h] for h in delta_long.Horizon]

plt1 =
    data(curve_long) *
    mapping(:Date, :Yield, color = :Label) *
    visual(Lines, linewidth = 2.2)

fig1 = draw(
    plt1;
    axis = (
        title = "Government Yield Curve History by Tenor",
        xlabel = "",
        ylabel = "Yield (%)"
    ),
    legend = (
        title = "Maturity",
        position = :right
    ),
    figure = (
        size = (1500, 750),
    )
)

fig1
save_both(fig1, "01_yield_curve_history")

# Using a Scatter plot with large square markers to simulate a heatmap grid
# This is more robust in AlgebraOfGraphics for time-series / maturity axes
plt2 =
    data(curve_long) *
    mapping(:Date, :Years, color = :Yield) *
    visual(Scatter, marker = :rect, markersize = 15)

fig2 = draw(
    plt2;
    axis = (
        title = "Yield Surface Heatmap",
        xlabel = "",
        ylabel = "Maturity (Years)",
        yticks = (tenor_map.Years, tenor_map.Label)
    ),
    colorbar = (
        label = "Yield (%)",
    ),
    figure = (
        size = (1500, 650),
    )
)

display(fig2)
save_both(fig2, "02_yield_surface_heatmap")

plt3 =
    data(snapshots) *
    mapping(:Label, :Yield, color = :AsOf, group = :AsOf) *
    (
        visual(Lines, linewidth = 2.8) +
        visual(Scatter, markersize = 11)
    )

fig3 = draw(
    plt3;
    axis = (
        title = "Yield Curve Snapshots: Latest vs 1M / 6M / 1Y / 5Y Ago",
        xlabel = "Maturity",
        ylabel = "Yield (%)"
    ),
    legend = (
        title = "Curve Date",
        position = :right
    ),
    figure = (
        size = (1450, 750),
    )
)

fig3
save_both(fig3, "03_curve_snapshots")

using Statistics
using DataFrames
using Dates
using AlgebraOfGraphics
using CairoMakie

df = deepcopy(raw)

# Helper: mean of non-missing values, or missing if all are missing
safe_mean(v) = begin
    vals = collect(skipmissing(v))
    isempty(vals) ? missing : mean(vals)
end

# Helper for 2-point spread
safe_spread(a, b) = (ismissing(a) || ismissing(b)) ? missing : (a - b)

# Helper for curvature
safe_curvature(y2, y5, y10) = (
    ismissing(y2) || ismissing(y5) || ismissing(y10)
) ? missing : (2y5 - y2 - y10)

# Factors
df.Level = [
    safe_mean([
        r.TBond_2y,
        r.TBond_5y,
        r.TBond_10y,
        r.TBond_15y,
        r.TBond_20y
    ])
    for r in eachrow(df)
]

df.Slope_10y_2y = [
    safe_spread(r.TBond_10y, r.TBond_2y)
    for r in eachrow(df)
]

df.Slope_20y_91d = [
    safe_spread(r.TBond_20y, r.TBill_91d)
    for r in eachrow(df)
]

df.Curvature_5y = [
    safe_curvature(r.TBond_2y, r.TBond_5y, r.TBond_10y)
    for r in eachrow(df)
]

# Long format
factors = stack(
    select(df, :Date, :Level, :Slope_10y_2y, :Slope_20y_91d, :Curvature_5y),
    Not(:Date),
    variable_name = :Metric,
    value_name = :Value
)

# Normalize metric names to String
factors.Metric = String.(factors.Metric)

metric_labels = Dict(
    "Level" => "Level Factor",
    "Slope_10y_2y" => "Slope Factor: 10Y - 2Y",
    "Slope_20y_91d" => "Slope Factor: 20Y - 91D",
    "Curvature_5y" => "Curvature Factor: 2×5Y - 2Y - 10Y"
)

factors.Label = [metric_labels[m] for m in factors.Metric]

# Drop missing factor values BEFORE plotting
factors_plot = dropmissing(select(factors, :Date, :Metric, :Label, :Value))

# Force display order
ordered_labels = [
    "Level Factor",
    "Slope Factor: 10Y - 2Y",
    "Slope Factor: 20Y - 91D",
    "Curvature Factor: 2×5Y - 2Y - 10Y"
]

factors_plot.Label = categorical(
    factors_plot.Label;
    ordered = true,
    levels = ordered_labels
)

# Quick diagnostic
combine(groupby(factors_plot, :Label), nrow => :ObsCount)

plt4 =
    data(factors_plot) *
    mapping(:Date, :Value, layout = :Label) *
    visual(Lines, linewidth = 2.4, color = :black)

fig4 = draw(
    plt4;
    facet = (linkyaxes = false,),
    axis = (
        xlabel = "",
        ylabel = "",
        xgridcolor = :gray95,
        ygridcolor = :gray95,
        topspinevisible = false,
        rightspinevisible = false,
        xticklabelrotation = 45
    ),
    figure = (
        size = (1200, 900),
    )
)

# Iterate through the axes to add reference lines and tighten scaling
for elem in fig4.figure.content
    if elem isa Axis
        # Add zero reference line
        hlines!(elem, [0.0], color = :gray50, linestyle = :dash, linewidth = 1.2)

        # Optional: tighten x-limits to the data range
        autolimits!(elem)
    end
end

Label(
    fig4.figure[0, 1:2],
    "Yield Curve Factor Decomposition (Quadrant View)",
    fontsize = 20,
    font = :bold,
    padding = (0, 0, 20, 0)
)

fig4
save_both(fig4, "04_curve_factor_decomposition_quadrants")

plt5 =
    data(delta_long) *
    mapping(:Label, :ChangeBP, color = :HorizonLabel, dodge = :HorizonLabel) *
    visual(BarPlot)

fig5 = draw(
    plt5;
    axis = (
        title = "Change in Yield by Tenor (bp): Latest vs 1M / 6M / 1Y",
        xlabel = "Maturity",
        ylabel = "Change (bp)"
    ),
    legend = (
        title = "Horizon",
        position = :right
    ),
    figure = (
        size = (1450, 750),
    )
)

fig5
save_both(fig5, "05_change_by_tenor_bp")
