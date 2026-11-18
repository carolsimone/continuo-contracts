{% test is_positive(model, column_name, min_value=0) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} <= {{ min_value }}
{% endtest %}
